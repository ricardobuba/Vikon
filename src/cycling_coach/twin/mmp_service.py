"""MMP persistida: calcular una vez la curva de potencia de cada actividad.

El motor de CP no consume el stream crudo, consume su curva máxima media (MMP).
Recalcularla en cada arranque costaba cargar ~200 MB de series para obtener
~120 bytes por actividad, y era el 100% del tiempo del precalentado.

Al persistirla se consigue además algo que no es solo velocidad: los streams
antiguos dejan de ser imprescindibles, así que se pueden soltar sin perder la
trayectoria histórica de CP.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from cycling_coach.db.repositories import (
    activities_without_mmp,
    upsert_activity_mmp,
)
from cycling_coach.metrics import mean_maximal_power
from cycling_coach.metrics.cleaning import clean_power

_log = logging.getLogger(__name__)

# UNIÓN de todas las duraciones que usa el código (filtro de CP, coherencia,
# curva de potencia, calibración del CRI). Guardar un superconjunto cuesta
# bytes; quedarse corto rompería un consumidor en silencio.
MMP_DURATIONS: tuple[int, ...] = (
    5, 15, 30, 60, 120, 180, 240, 300, 420, 600, 900, 1200, 1800, 2700, 3600,
)

# Súbelo si cambian `clean_power` o `mean_maximal_power`: lo guardado con una
# versión anterior se considera obsoleto y se recalcula.
MMP_VERSION = 1


def compute_mmp(watts: list, sample_hz: float = 1.0) -> tuple[dict, dict]:
    """(MMP cruda, MMP limpia) de una serie de potencia.

    Las dos variantes NO son un lujo: el filtro de CP trabaja sobre la señal
    limpia y la coherencia sobre la cruda. Guardar solo una cambiaría el
    comportamiento de la otra sin que nadie se enterara.
    """
    raw = mean_maximal_power(watts, MMP_DURATIONS)
    clean = mean_maximal_power(clean_power(watts, sample_hz), MMP_DURATIONS)
    return (
        {str(k): float(v) for k, v in raw.items()},
        {str(k): float(v) for k, v in clean.items()},
    )


def store_mmp(
    session: Session, athlete_id: int, activity_id: int,
    start_time: datetime, watts: list, sample_hz: float = 1.0,
) -> None:
    """Calcula y persiste la MMP de una actividad (idempotente)."""
    raw, clean = compute_mmp(watts, sample_hz)
    upsert_activity_mmp(
        session, activity_id=activity_id, athlete_id=athlete_id,
        start_time=start_time, version=MMP_VERSION, mmp_raw=raw, mmp_clean=clean,
    )


def backfill_mmp(
    session: Session, athlete_id: int, batch: int | None = None
) -> int:
    """Rellena la MMP de las actividades que aún no la tienen (o la tienen con
    una versión vieja). Devuelve cuántas se calcularon.

    Idempotente y reanudable: si se corta, la siguiente pasada sigue donde iba.
    """
    pending = activities_without_mmp(session, athlete_id, MMP_VERSION, limit=batch)
    n = 0
    for activity_id, start_time, watts in pending:
        if not watts:
            continue
        store_mmp(session, athlete_id, activity_id, start_time, watts)
        n += 1
    if n:
        _log.info("MMP calculada para %d actividades (atleta %d).", n, athlete_id)
    return n
