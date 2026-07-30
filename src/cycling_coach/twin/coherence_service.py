"""Servicio de coherencia: contrasta el CP/W' vigente con la curva MMP real
reciente del atleta (Paso 3 de robustez). Sirve al CP actual: avisa si está
obsoleto (envolvente rota) o sin confirmar (esfuerzos submaximales)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from cycling_coach.db.repositories import (
    latest_parameter_estimate,
    load_power_mmp,
)
from cycling_coach.physiology.coherence import CoherenceReport, assess_coherence

# Duraciones CP-relevantes (de 1 min a 60 min).
_DURATIONS_S = [60, 120, 180, 300, 600, 900, 1200, 1800, 2700, 3600]

# Curva de potencia completa: incluye el tramo anaeróbico/sprint (5 s–1 min) que
# el CP de 2 parámetros NO modela, para verla entera (importa a un puncheur).
_CURVE_DURATIONS_S = [5, 15, 30, 60, 120, 300, 600, 900, 1200, 1800, 2700, 3600]
# Por debajo de esto el modelo 2-param (CP+W'/t) no es fiable (→∞): no lo dibujamos.
_MODEL_MIN_S = 120


def recent_mmp(
    session: Session, athlete_id: int, as_of: date, days: int
) -> dict[int, float]:
    """Curva MMP agregada (mejor por duración) de las actividades con potencia
    de los últimos `days` días."""
    cutoff = as_of - timedelta(days=days)
    agg: dict[int, float] = {}
    # MMP CRUDA a propósito: es lo que usaba este camino antes de persistirla,
    # y cambiar a la limpia alteraría el veredicto de coherencia en silencio.
    for start, _aid, mmp_raw, _clean in load_power_mmp(session, athlete_id):
        if start.date() < cutoff:
            continue
        for secs in _DURATIONS_S:
            power = mmp_raw.get(secs)
            if power is not None and power > agg.get(secs, 0.0):
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


def power_curve(
    session: Session, athlete_id: int, as_of: date, days: int = 120
) -> dict | None:
    """Curva de potencia real (mejor por duración, 5 s–1 h) + predicción del
    modelo CP/W' en el tramo donde es válido (≥2 min) + veredicto de coherencia.
    Una sola pasada por los streams. None si no hay potencia reciente."""
    cp = latest_parameter_estimate(session, athlete_id, "cp")
    wp = latest_parameter_estimate(session, athlete_id, "w_prime")
    cutoff = as_of - timedelta(days=days)
    agg: dict[int, float] = {}
    for start, _aid, mmp_raw, _clean in load_power_mmp(session, athlete_id):
        if start.date() < cutoff:
            continue
        for secs in _CURVE_DURATIONS_S:
            power = mmp_raw.get(secs)
            if power is not None and power > agg.get(secs, 0.0):
                agg[secs] = power
    if not agg:
        return None

    points = []
    for secs in _CURVE_DURATIONS_S:
        actual = agg.get(secs)
        predicted = (
            cp + wp / secs if (cp and wp and secs >= _MODEL_MIN_S) else None
        )
        points.append(
            {"seconds": secs, "actual": actual, "predicted": predicted}
        )

    report = None
    if cp and wp:
        sub = {s: p for s, p in agg.items() if s >= 60}
        if sub:
            report = assess_coherence(cp, wp, sub)
    return {
        "cp": cp,
        "w_prime": wp,
        "points": points,
        "verdict": report.verdict if report else None,
        "coherent": report.coherent if report else None,
    }
