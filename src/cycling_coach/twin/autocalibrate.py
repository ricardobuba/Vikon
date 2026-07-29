"""Autocalibración del CP/W'/FTP: reestimar cuando llegan datos que importan.

Hasta ahora el motor solo recalculaba con `cc estimate-cp` a mano, así que el
plan usaba un FTP que podía quedarse viejo (y los vatios de cada sesión salen de
él). Aquí se recalcula SOLO cuando hay motivo:

- han entrado actividades con potencia nuevas desde la última estimación, y
- ha pasado un mínimo de días (evita repetir un cálculo caro sin datos nuevos).

La reestimación completa mide ~2-3 s con >1000 actividades (medido), así que se
lanza en segundo plano tras la sincronización, no en la ruta de la petición.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cycling_coach.db.models import Activity, ParameterEstimate
from cycling_coach.db.repositories import store_parameter_estimate
from cycling_coach.domain.models import Estimate
from cycling_coach.twin.cp_estimation import estimate_cp

_log = logging.getLogger("uvicorn.error")

# No recalcular más de una vez cada tantos días si no hay razón de peso.
MIN_DAYS_BETWEEN = 3
# …salvo que hayan entrado al menos estas actividades con potencia nuevas.
FORCE_AFTER_ACTIVITIES = 3


@dataclass
class CalibrationOutcome:
    ran: bool
    reason: str
    ftp: float | None = None
    cp: float | None = None
    previous_ftp: float | None = None

    @property
    def delta_ftp(self) -> float | None:
        if self.ftp is None or self.previous_ftp is None:
            return None
        return self.ftp - self.previous_ftp


def _honest(mean: float, sd: float, as_of, source: str) -> Estimate:
    """Estimación con la incertidumbre demostrada (no la del estado latente)."""
    return Estimate(
        mean=mean, sd=sd, ci90=(mean - 1.645 * sd, mean + 1.645 * sd),
        updated_at=as_of, source=source,
    )


def _last_estimate(session: Session, athlete_id: int, param: str):
    return session.execute(
        select(ParameterEstimate)
        .where(
            ParameterEstimate.athlete_id == athlete_id,
            ParameterEstimate.param == param,
        )
        .order_by(ParameterEstimate.as_of.desc())
        .limit(1)
    ).scalar_one_or_none()


def _new_power_activities_since(
    session: Session, athlete_id: int, since: datetime
) -> int:
    return session.execute(
        select(func.count())
        .select_from(Activity)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.device_watts.is_(True),
            Activity.ingested_at > since,
        )
    ).scalar_one()


def should_recalibrate(
    session: Session, athlete_id: int, now: datetime | None = None
) -> tuple[bool, str]:
    """¿Toca reestimar? Devuelve (sí/no, motivo explicable)."""
    now = now or datetime.now(UTC)
    last = _last_estimate(session, athlete_id, "ftp")
    if last is None:
        return True, "no hay estimación previa"

    last_at = last.as_of
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=UTC)
    new_acts = _new_power_activities_since(session, athlete_id, last_at)
    if new_acts == 0:
        return False, "sin actividades con potencia nuevas"
    if new_acts >= FORCE_AFTER_ACTIVITIES:
        return True, f"{new_acts} actividades con potencia nuevas"
    if now - last_at >= timedelta(days=MIN_DAYS_BETWEEN):
        return True, f"{new_acts} nuevas y {MIN_DAYS_BETWEEN}+ días desde la última"
    return False, "estimación reciente y pocos datos nuevos"


def autocalibrate(
    session: Session, athlete_id: int, force: bool = False
) -> CalibrationOutcome:
    """Reestima CP/W'/FTP si hay motivo (o si `force`) y lo persiste."""
    ok, reason = (True, "forzado") if force else should_recalibrate(session, athlete_id)
    if not ok:
        return CalibrationOutcome(ran=False, reason=reason)

    prev = _last_estimate(session, athlete_id, "ftp")
    previous_ftp = prev.mean if prev else None

    result = estimate_cp(session, athlete_id)
    if result is None:
        return CalibrationOutcome(ran=False, reason="sin datos de potencia")

    cur = result.state
    # Incertidumbre HONESTA (el error de predicción medido en el backtest), la
    # misma que usa `cc estimate-cp`: nunca afirmamos más precisión de la validada.
    sd = result.predictive_sd_cp
    cp_est = _honest(cur.cp.mean, sd, cur.as_of, cur.cp.source)
    ftp_est = _honest(cur.ftp_w, sd, cur.as_of, cur.cp.source)
    store_parameter_estimate(session, athlete_id, "cp", cp_est)
    store_parameter_estimate(session, athlete_id, "w_prime", cur.w_prime)
    store_parameter_estimate(session, athlete_id, "ftp", ftp_est)

    return CalibrationOutcome(
        ran=True, reason=reason, ftp=ftp_est.mean, cp=cp_est.mean,
        previous_ftp=previous_ftp,
    )
