"""Servicio de carga de entrenamiento: calcula TSS por sesión y las series
CTL/ATL/TSB, y persiste el estado actual en la capa `daily` del gemelo."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from cycling_coach.db.repositories import (
    latest_parameter_estimate,
    load_activity_loads,
    upsert_daily_metric,
)
from cycling_coach.domain.models import CanonicalDailyMetric
from cycling_coach.physiology import compute_ctl_atl_tsb, training_stress_score
from cycling_coach.physiology.training_load import LoadPoint


@dataclass
class LoadResult:
    current: LoadPoint
    n_days: int
    n_activities: int
    ftp: float


def compute_and_store_load(
    session: Session, athlete_id: int, as_of: date, ftp: float | None = None
) -> LoadResult | None:
    """Calcula CTL/ATL/TSB hasta `as_of` con el FTP dado (o el último estimado)
    y guarda el estado actual en daily_metric. None si falta FTP o actividades."""
    if ftp is None:
        ftp = latest_parameter_estimate(session, athlete_id, "ftp")
    if not ftp:
        return None

    loads = load_activity_loads(session, athlete_id)
    if not loads:
        return None

    daily_tss: dict[date, float] = defaultdict(float)
    for day, duration_s, np_w in loads:
        daily_tss[day] += training_stress_score(np_w, duration_s, ftp)
    daily_tss.setdefault(as_of, 0.0)   # extender hasta hoy → CTL/ATL decaen

    series = compute_ctl_atl_tsb(daily_tss)
    last = series[-1]
    for metric, value in (("ctl", last.ctl), ("atl", last.atl), ("tsb", last.tsb)):
        upsert_daily_metric(
            session,
            athlete_id,
            CanonicalDailyMetric(metric=metric, day=last.day, value=value, source="computed"),
        )
    return LoadResult(current=last, n_days=len(series), n_activities=len(loads), ftp=ftp)
