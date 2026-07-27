"""Orquestador conversacional: intención → planner determinista → explicación.

El LLM aparece solo en los extremos (traducir la intención, redactar la
respuesta). La decisión —qué entrenar— siempre la toma el motor con la ficha.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from cycling_coach.assistant.grounding import Facts, gather_facts
from cycling_coach.assistant.llm import LLMClient, LLMError
from cycling_coach.assistant.prompts import EXPLAIN_SYSTEM, INTENT_SYSTEM, explain_user
from cycling_coach.db.repositories import upsert_daily_metric
from cycling_coach.domain.models import CanonicalDailyMetric

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


@dataclass
class Intent:
    kind: str = "plan"                 # "plan" | "question" | "log"
    minutes: float | None = None
    readiness: str | None = None       # "low" | "normal" | "high"
    question: str | None = None
    log: dict[str, float] = field(default_factory=dict)   # datos a registrar

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


@dataclass
class Reply:
    text: str                          # respuesta en lenguaje natural
    intent: Intent
    facts: Facts
    logged: dict[str, float] = field(default_factory=dict)   # datos registrados


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

    return Intent(
        kind=kind if kind in ("question", "log") else "plan",
        minutes=float(minutes) if isinstance(minutes, int | float) else None,
        readiness=readiness if readiness in ("low", "normal", "high") else None,
        question=data.get("question") or None,
        log=log,
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
    text = llm.complete(EXPLAIN_SYSTEM, explain_user(block, question))
    return Reply(text=text.strip(), intent=intent, facts=facts, logged=logged)


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
        )
