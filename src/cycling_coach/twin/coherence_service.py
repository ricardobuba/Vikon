"""Servicio de coherencia: contrasta el CP/W' vigente con la curva MMP real
reciente del atleta (Paso 3 de robustez). Sirve al CP actual: avisa si está
obsoleto (envolvente rota) o sin confirmar (esfuerzos submaximales)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from cycling_coach.db.repositories import (
    latest_parameter_estimate,
    load_power_activities,
)
from cycling_coach.metrics.power import mean_maximal_power
from cycling_coach.physiology.coherence import CoherenceReport, assess_coherence

# Duraciones CP-relevantes (de 1 min a 60 min).
_DURATIONS_S = [60, 120, 180, 300, 600, 900, 1200, 1800, 2700, 3600]


def recent_mmp(
    session: Session, athlete_id: int, as_of: date, days: int
) -> dict[int, float]:
    """Curva MMP agregada (mejor por duración) de las actividades con potencia
    de los últimos `days` días."""
    cutoff = as_of - timedelta(days=days)
    agg: dict[int, float] = {}
    for start, _aid, watts in load_power_activities(session, athlete_id):
        if start.date() < cutoff:
            continue
        for secs, power in mean_maximal_power(watts, _DURATIONS_S).items():
            if power > agg.get(secs, 0.0):
                agg[secs] = power
    return agg


def assess_cp_coherence(
    session: Session, athlete_id: int, as_of: date, days: int = 120
) -> CoherenceReport | None:
    """Informe de coherencia del CP vigente contra los últimos `days` días.
    None si falta el estimador o no hay potencia reciente."""
    cp = latest_parameter_estimate(session, athlete_id, "cp")
    wp = latest_parameter_estimate(session, athlete_id, "w_prime")
    if cp is None or wp is None:
        return None
    mmp = recent_mmp(session, athlete_id, as_of, days)
    if not mmp:
        return None
    return assess_coherence(cp, wp, mmp)
