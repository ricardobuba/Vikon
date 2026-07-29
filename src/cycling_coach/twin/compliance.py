"""Cumplimiento del plan: qué se prescribió vs qué se hizo de verdad.

Cierra el bucle del sistema. Hasta ahora el plan se emitía y nadie comprobaba si
se seguía, así que:
- el CRI tenía un componente de cumplimiento SIEMPRE vacío (cobertura 75%);
- no se sabía si un plan que "no funciona" es que estaba mal o que no se hizo.

La comparación es determinista y honesta: el TIPO real de sesión sale de la
distribución de potencia (`metrics.session_type`), no de la media, y la carga se
compara en TSS. No se juzga al ciclista: se mide, para que el motor aprenda.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from cycling_coach.db.models import Activity
from cycling_coach.db.repositories import (
    latest_parameter_estimate,
    load_watts_stream,
)
from cycling_coach.metrics.session_type import classify
from cycling_coach.physiology.training_load import training_stress_score
from cycling_coach.planner.library import Objective
from cycling_coach.planner.planner import INTENSITY_RANK

# Márgenes: por debajo de esto la carga se considera "de menos"/"de más". Un
# plan no es un contrato: ±25% de TSS sigue siendo "cumplido".
TSS_TOLERANCE = 0.25
# Se considera que hubo sesión a partir de estos minutos (evita contar traslados).
MIN_SESSION_MIN = 15

# Crédito de cada estado para el componente "cumplimiento" del CRI. No es
# binario a propósito: entrenar algo distinto NO es lo mismo que no entrenar, y
# castigarlo igual daría una señal falsa al índice.
STATUS_CREDIT: dict[str, float] = {
    "cumplido": 1.0,
    "descanso_ok": 1.0,
    "más": 0.7,          # se pasó de carga: se hizo el trabajo, con exceso
    "menos": 0.6,
    "distinto": 0.6,     # entrenó, pero no lo prescrito
    "extra": 0.7,        # sesión no planificada: no es incumplir
    "no_hecho": 0.0,
}
# Con menos días registrados que esto el ratio no dice nada: se declara ausente
# (el CRI renormaliza pesos) en vez de inventar un número.
MIN_DAYS_FOR_SCORE = 3


@dataclass
class DayCompliance:
    day: date
    planned_objective: str | None
    planned_tss: float | None
    done_kind: str | None            # tipo REAL medido (por zonas)
    done_tss: float | None
    done_minutes: float | None
    status: str                      # cumplido|más|menos|distinto|no_hecho|extra|descanso_ok
    note: str

    @property
    def followed(self) -> bool:
        return self.status in ("cumplido", "descanso_ok")


@dataclass
class ComplianceReport:
    days: list[DayCompliance]
    rate: float                      # fracción de días seguidos (0–1)
    n_planned: int
    n_followed: int
    tss_planned: float
    tss_done: float

    @property
    def load_ratio(self) -> float | None:
        return self.tss_done / self.tss_planned if self.tss_planned else None

    @property
    def score(self) -> float | None:
        """Adherencia en [0,1] para el CRI, con crédito parcial por estado.
        None si hay pocos días: mejor un hueco declarado que un dato inventado."""
        if len(self.days) < MIN_DAYS_FOR_SCORE:
            return None
        credits = [STATUS_CREDIT.get(d.status, 0.5) for d in self.days]
        return sum(credits) / len(credits)


def _status(
    planned: str | None, planned_tss: float | None,
    done_kind: str | None, done_tss: float | None,
) -> tuple[str, str]:
    """Compara prescripción y realidad. Devuelve (estado, explicación)."""
    if planned is None:
        if done_kind is None:
            return "descanso_ok", "sin plan y sin sesión"
        return "extra", f"sesión no planificada ({done_kind})"

    if planned == Objective.rest.value:
        if done_kind is None:
            return "descanso_ok", "descanso respetado"
        return "extra", f"tocaba descansar y entrenaste ({done_kind})"

    if done_kind is None:
        return "no_hecho", f"tocaba {planned.replace('_', ' ')} y no hubo sesión"

    # ¿El TIPO coincide? Se acepta un escalón de diferencia: un sweet spot que
    # acaba en umbral sigue siendo el mismo trabajo de calidad.
    try:
        want = INTENSITY_RANK[Objective(planned)]
        got = INTENSITY_RANK[Objective(done_kind)]
        close = abs(want - got) <= 1
    except (ValueError, KeyError):
        close = planned == done_kind

    if not close:
        return "distinto", (
            f"tocaba {planned.replace('_', ' ')} e hiciste {done_kind.replace('_', ' ')}"
        )
    if planned_tss and done_tss:
        ratio = done_tss / planned_tss
        if ratio > 1 + TSS_TOLERANCE:
            return "más", f"{ratio:.0%} de la carga prevista"
        if ratio < 1 - TSS_TOLERANCE:
            return "menos", f"{ratio:.0%} de la carga prevista"
    return "cumplido", "hecho como estaba previsto"


def _done_on(
    session: Session, athlete_id: int, day: date, ftp: float | None
) -> tuple[str | None, float | None, float | None]:
    """(tipo real, TSS, minutos) de lo entrenado ese día. La de mayor carga."""
    acts = session.execute(
        select(Activity).where(
            Activity.athlete_id == athlete_id,
            Activity.start_time >= day,
            Activity.start_time < day + timedelta(days=1),
        )
    ).scalars().all()
    best: tuple[str | None, float | None, float | None] = (None, None, None)
    best_tss = -1.0
    for a in acts:
        secs = a.moving_time_s or a.elapsed_time_s or 0
        minutes = secs / 60
        if minutes < MIN_SESSION_MIN:
            continue
        tss = (
            training_stress_score(a.weighted_avg_power_w, secs, ftp)
            if (ftp and a.weighted_avg_power_w and secs) else None
        )
        kind = None
        if ftp:
            watts = load_watts_stream(session, a.id)
            if watts:
                kind = classify(watts, ftp).kind
        if kind is None:
            kind = "endurance"          # hubo sesión aunque no sepamos el tipo
        if (tss or 0) > best_tss:
            best_tss = tss or 0
            best = (kind, tss, round(minutes, 1))
    return best


def compliance_report(
    session: Session,
    athlete_id: int,
    plan_by_day: dict[date, tuple[str, float]],
) -> ComplianceReport:
    """Compara el plan que se dio (día → (objetivo, TSS)) con lo entrenado.

    `plan_by_day` lo aporta quien lo tenga registrado: hoy el horizonte no se
    persiste, así que el llamador reconstruye la prescripción de esos días."""
    ftp = latest_parameter_estimate(session, athlete_id, "ftp")
    days: list[DayCompliance] = []
    n_planned = n_followed = 0
    tss_planned = tss_done = 0.0

    for day in sorted(plan_by_day):
        objective, ptss = plan_by_day[day]
        kind, dtss, dmin = _done_on(session, athlete_id, day, ftp)
        status, note = _status(objective, ptss, kind, dtss)
        if objective and objective != Objective.rest.value:
            n_planned += 1
            tss_planned += ptss or 0
        tss_done += dtss or 0
        if status in ("cumplido", "descanso_ok"):
            n_followed += 1
        days.append(
            DayCompliance(
                day=day, planned_objective=objective, planned_tss=ptss,
                done_kind=kind, done_tss=round(dtss) if dtss else None,
                done_minutes=dmin, status=status, note=note,
            )
        )

    rate = n_followed / len(days) if days else 0.0
    return ComplianceReport(
        days=days, rate=rate, n_planned=n_planned, n_followed=n_followed,
        tss_planned=round(tss_planned), tss_done=round(tss_done),
    )
