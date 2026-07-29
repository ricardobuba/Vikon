"""Orquestador conversacional: intención → planner determinista → explicación.

El LLM aparece solo en los extremos (traducir la intención, redactar la
respuesta). La decisión —qué entrenar— siempre la toma el motor con la ficha.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from cycling_coach.assistant.grounding import Facts, gather_facts
from cycling_coach.assistant.llm import LLMClient, LLMError
from cycling_coach.assistant.prompts import EXPLAIN_SYSTEM, INTENT_SYSTEM, explain_user
from cycling_coach.db.repositories import (
    find_activity_on_date,
    latest_power_activity,
    save_profile,
    set_availability,
    store_parameter_estimate,
    upsert_daily_metric,
)
from cycling_coach.domain.models import CanonicalDailyMetric, Estimate

# Disposición subjetiva → CRI efectivo (determinista). "low" cruza el umbral de
# recovery (<40); "high" habilita calidad alta si además la forma acompaña.
_READINESS_CRI = {"low": 30.0, "high": 80.0}

# Datos que el chat puede REGISTRAR (grieta "log"): clave del intent → métrica
# diaria, con rango plausible (guarda anti-alucinación del LLM).
_LOG_METRIC = {
    "weight_kg": "body_mass_kg",
    "sleep_hours": "sleep_hours",
    "resting_hr": "resting_hr",
    "hrv_rmssd": "hrv_rmssd",
    "feel": "readiness",
}
_LOG_RANGE = {
    "weight_kg": (30.0, 200.0),
    "sleep_hours": (0.0, 16.0),
    "resting_hr": (25.0, 120.0),
    "hrv_rmssd": (3.0, 250.0),
    "feel": (1.0, 10.0),
}
_LOG_LABEL = {
    "weight_kg": "peso {:.1f} kg",
    "sleep_hours": "sueño {:.1f} h",
    "resting_hr": "FC reposo {:.0f}",
    "hrv_rmssd": "HRV {:.0f}",
    "feel": "sensación {:.0f}/10",
}


# --- Datos PERMANENTES del ciclista que el chat puede cambiar ----------------
# Rango plausible por campo: filtro anti-alucinación (el LLM traduce, no decide).
_PROFILE_RANGE = {
    "ftp": (60.0, 600.0),
    "weight_kg": (30.0, 200.0),
    "height_cm": (120.0, 230.0),
    "hr_max": (120.0, 230.0),
    "hr_rest": (25.0, 120.0),
    "weekly_minutes_target": (0.0, 3000.0),
}
_PROFILE_LABEL = {
    "ftp": "FTP {:.0f} W",
    "weight_kg": "peso {:.1f} kg",
    "height_cm": "altura {:.0f} cm",
    "hr_max": "FC máx {:.0f}",
    "hr_rest": "FC reposo {:.0f}",
    "weekly_minutes_target": "objetivo semanal {:.0f} min",
    "level": "nivel {}",
    "availability": "disponibilidad {}",
}
_LEVELS = {"principiante", "intermedio", "avanzado", "elite"}
_DAY_MINUTES_MAX = 600.0
_DAY_NAMES = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


@dataclass
class Intent:
    kind: str = "plan"                 # "plan" | "question" | "log"
    minutes: float | None = None
    readiness: str | None = None       # "low" | "normal" | "high"
    question: str | None = None
    log: dict[str, float] = field(default_factory=dict)   # datos a registrar
    profile: dict = field(default_factory=dict)           # datos permanentes
    activity: dict = field(default_factory=dict)          # corrección de un entreno

    @property
    def cri_override(self) -> float | None:
        return _READINESS_CRI.get(self.readiness or "")


def log_metrics(
    session: Session, athlete_id: int, day: date, log: dict[str, float]
) -> dict[str, float]:
    """Persiste en daily_metric los datos reportados (peso, sueño, pulso, HRV,
    sensación) como fuente 'manual'. Devuelve lo guardado."""
    for key, value in log.items():
        upsert_daily_metric(
            session, athlete_id,
            CanonicalDailyMetric(_LOG_METRIC[key], day, value, "manual"),
        )
    return log


def _logged_note(log: dict[str, float]) -> str:
    return ", ".join(_LOG_LABEL[k].format(v) for k, v in log.items())


def apply_profile(session: Session, athlete_id: int, prof: dict) -> dict:
    """Escribe los datos PERMANENTES que pidió cambiar (perfil + disponibilidad).

    El FTP declarado se guarda como estimación de alta confianza para que el
    motor lo use ya (los vatios del plan salen de él). Devuelve lo aplicado."""
    applied: dict = {}
    fields = {k: v for k, v in prof.items() if k not in ("availability", "ftp", "level")}
    data = {}
    for key, value in fields.items():
        rng = _PROFILE_RANGE.get(key)
        if rng and isinstance(value, int | float) and not isinstance(value, bool):
            if rng[0] <= value <= rng[1]:
                data[key] = int(value) if key in ("hr_max", "hr_rest",
                                                  "weekly_minutes_target") else float(value)
                applied[key] = data[key]
    if isinstance(prof.get("level"), str) and prof["level"].lower() in _LEVELS:
        data["level"] = prof["level"].lower()
        applied["level"] = data["level"]
    if isinstance(prof.get("ftp"), int | float) and not isinstance(prof["ftp"], bool):
        lo, hi = _PROFILE_RANGE["ftp"]
        if lo <= prof["ftp"] <= hi:
            data["declared_ftp_w"] = float(prof["ftp"])
            applied["ftp"] = float(prof["ftp"])

    if data:
        save_profile(session, athlete_id, data)
    if "ftp" in applied:
        # Que el plan use ya el FTP declarado (es el que rinde los vatios). SD
        # moderada: lo dices tú, no sale de un test medido.
        ftp = applied["ftp"]
        now = datetime.now(UTC)
        store_parameter_estimate(
            session, athlete_id, "ftp",
            Estimate(
                mean=ftp, sd=10.0, ci90=(ftp - 16.5, ftp + 16.5),
                updated_at=now, source="test",
            ),
        )

    avail = prof.get("availability")
    if isinstance(avail, dict):
        per_day = {}
        for k, v in avail.items():
            try:
                wd = int(k)
            except (TypeError, ValueError):
                continue
            if 0 <= wd <= 6 and isinstance(v, int | float) and 0 <= v <= _DAY_MINUTES_MAX:
                per_day[wd] = int(v)
        if per_day:
            set_availability(session, athlete_id, per_day)
            applied["availability"] = {_DAY_NAMES[k]: v for k, v in sorted(per_day.items())}
    return applied


def apply_activity(session: Session, athlete_id: int, act: dict, today: date) -> dict:
    """Corrige un entrenamiento ya hecho: marcarlo como test maximal (ancla el
    CP/FTP con su curva real) y/o registrar sensación/RPE de ESE día."""
    applied: dict = {}
    raw = act.get("date")
    activity = None
    if raw == "last" or raw is None:
        activity = latest_power_activity(session, athlete_id)
    else:
        try:
            activity = find_activity_on_date(session, athlete_id, date.fromisoformat(str(raw)))
        except ValueError:
            activity = None
    if activity is None:
        return applied
    day = activity.start_time.date()

    if act.get("maximal_test") is True:
        activity.is_maximal_test = True
        session.flush()
        applied["maximal_test"] = day.isoformat()

    for key, metric in (("feel", "readiness"), ("rpe", "rpe")):
        v = act.get(key)
        if isinstance(v, int | float) and not isinstance(v, bool) and 1 <= v <= 10:
            upsert_daily_metric(
                session, athlete_id,
                CanonicalDailyMetric(metric, day, float(v), "manual"),
            )
            applied[key] = float(v)
    return applied


def _apply_changes(
    session: Session, athlete_id: int, intent: Intent, today: date
) -> tuple[dict, dict] | None:
    """Aplica los cambios de perfil/entreno pedidos. None si no hubo ninguno."""
    prof = apply_profile(session, athlete_id, intent.profile) if intent.profile else {}
    act = apply_activity(session, athlete_id, intent.activity, today) if intent.activity else {}
    return (prof, act) if (prof or act) else None


def _changed_dict(changed: tuple[dict, dict] | None) -> dict:
    if not changed:
        return {}
    prof, act = changed
    return {**prof, **{f"actividad_{k}": v for k, v in act.items()}}


def _changes_note(profile: dict, activity: dict) -> str:
    parts = [
        _PROFILE_LABEL.get(k, k + " {}").format(v) for k, v in profile.items()
    ]
    if activity.get("maximal_test"):
        parts.append(f"entreno del {activity['maximal_test']} marcado como test máximo")
    if "feel" in activity:
        parts.append(f"sensación de ese día {activity['feel']:.0f}/10")
    if "rpe" in activity:
        parts.append(f"RPE de ese día {activity['rpe']:.0f}/10")
    return ", ".join(parts)


@dataclass
class Reply:
    text: str                          # respuesta en lenguaje natural
    intent: Intent
    facts: Facts
    logged: dict[str, float] = field(default_factory=dict)   # datos registrados
    changed: dict = field(default_factory=dict)              # perfil/entreno modificados


def parse_intent(llm: LLMClient, message: str) -> Intent:
    """El LLM traduce el texto libre a inputs estructurados (no decide nada)."""
    data = llm.extract_json(INTENT_SYSTEM, message)
    kind = data.get("kind")
    minutes = data.get("minutes")
    readiness = data.get("readiness")

    raw_log = data.get("log") or {}
    log: dict[str, float] = {}
    if isinstance(raw_log, dict):
        for key, (lo, hi) in _LOG_RANGE.items():
            v = raw_log.get(key)
            if isinstance(v, int | float) and not isinstance(v, bool) and lo <= v <= hi:
                log[key] = float(v)      # descarta valores fuera de rango (alucinación)

    prof = data.get("profile")
    act = data.get("activity")
    return Intent(
        kind=kind if kind in ("question", "log") else "plan",
        minutes=float(minutes) if isinstance(minutes, int | float) else None,
        readiness=readiness if readiness in ("low", "normal", "high") else None,
        question=data.get("question") or None,
        log=log,
        profile=prof if isinstance(prof, dict) else {},
        activity=act if isinstance(act, dict) else {},
    )


def ask(
    session: Session,
    athlete_id: int,
    as_of: date,
    message: str,
    llm: LLMClient | None = None,
) -> Reply:
    """Una vuelta completa: interpreta el mensaje, deja que el motor decida con
    esos inputs, y redacta la respuesta anclada a la ficha."""
    llm = llm or LLMClient.from_settings()
    intent = parse_intent(llm, message)

    logged = log_metrics(session, athlete_id, as_of, intent.log) if intent.log else {}
    changed = _apply_changes(session, athlete_id, intent, as_of)
    # Si registró su sensación, el CRI ya la usa (dato real) → sin override.
    cri_override = None if "feel" in logged else intent.cri_override

    facts = gather_facts(
        session, athlete_id, as_of, minutes=intent.minutes, cri_override=cri_override,
        with_horizon=True,
    )
    question = intent.question if intent.kind == "question" else None
    block = facts.to_prompt()
    if logged:
        block += f"\n\nEl ciclista ACABA DE REGISTRAR (confírmalo): {_logged_note(logged)}."
    if changed:
        block += f"\n\nDATOS ACTUALIZADOS (confírmalo): {_changes_note(*changed)}."
    text = llm.complete(EXPLAIN_SYSTEM, explain_user(block, question))
    return Reply(
        text=text.strip(), intent=intent, facts=facts, logged=logged,
        changed=_changed_dict(changed),
    )


def explain_today(
    session: Session, athlete_id: int, as_of: date, llm: LLMClient | None = None
) -> Reply:
    """Narra el plan de hoy en lenguaje natural (sin traducir intención)."""
    llm = llm or LLMClient.from_settings()
    facts = gather_facts(session, athlete_id, as_of)
    if facts.plan is None:
        raise LLMError("No hay plan (falta FTP: corre `cc estimate-cp`).")
    text = llm.complete(EXPLAIN_SYSTEM, explain_user(facts.to_prompt(), None))
    return Reply(text=text.strip(), intent=Intent(kind="plan"), facts=facts)


@dataclass
class ChatSession:
    """Conversación multivuelta con Vikon. Mantiene el historial y una intención
    PEGAJOSA: los minutos y la disposición persisten hasta que los cambies (así
    "40 min" seguido de "¿por qué?" sigue hablando del plan de 40 min). La ficha
    se recalcula cada vuelta con el estado vigente — el motor siempre decide.

    No retiene la sesión de BD (se pasa por turno) para servir también a la web,
    donde cada petición usa una conexión fresca."""

    athlete_id: int
    as_of: date
    llm: LLMClient
    minutes: float | None = None
    readiness: str | None = None
    history: list[dict[str, str]] = field(default_factory=list)

    def turn(self, session: Session, message: str) -> Reply:
        intent = parse_intent(self.llm, message)
        if intent.minutes is not None:
            self.minutes = intent.minutes
        if intent.readiness is not None:
            self.readiness = intent.readiness

        logged = log_metrics(session, self.athlete_id, self.as_of, intent.log) if intent.log else {}
        changed = _apply_changes(session, self.athlete_id, intent, self.as_of)
        if "feel" in logged:
            self.readiness = None            # el valor registrado manda; no arrastrar override
        cri_override = _READINESS_CRI.get(self.readiness or "")

        facts = gather_facts(
            session, self.athlete_id, self.as_of,
            minutes=self.minutes, cri_override=cri_override, with_horizon=True,
        )
        block = facts.to_prompt()
        if logged:
            block += f"\n\nEl ciclista ACABA DE REGISTRAR (confírmalo): {_logged_note(logged)}."
        system = f"{EXPLAIN_SYSTEM}\n\nFICHA ACTUAL (única fuente de cifras):\n{block}"
        messages = [
            {"role": "system", "content": system},
            *self.history,
            {"role": "user", "content": message},
        ]
        text = self.llm.chat(messages).strip()

        self.history.append({"role": "user", "content": message})
        self.history.append({"role": "assistant", "content": text})
        self.history = self.history[-12:]        # acota el contexto (6 turnos)
        return Reply(
            text=text,
            intent=Intent(
                kind=intent.kind, minutes=self.minutes,
                readiness=self.readiness, question=intent.question, log=intent.log,
            ),
            facts=facts,
            logged=logged,
            changed=_changed_dict(changed),
        )
