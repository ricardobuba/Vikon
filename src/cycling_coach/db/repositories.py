"""Operaciones de persistencia idempotentes (upsert) para la ingesta.

El backfill puede reejecutarse sin duplicar: usamos ON CONFLICT sobre las
restricciones únicas naturales (provider_activity_id, etc.).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cycling_coach.db.models import Activity, DailyMetric, Stream
from cycling_coach.domain.models import (
    CanonicalActivity,
    CanonicalDailyMetric,
    CanonicalStream,
)


def upsert_activity(session: Session, athlete_id: int, act: CanonicalActivity) -> int:
    """Inserta o actualiza una actividad. Devuelve su id interno."""
    values = {
        "athlete_id": athlete_id,
        "provider": act.provider,
        "provider_activity_id": act.provider_activity_id,
        "start_time": act.start_time,
        "sport": act.sport.value,
        "name": act.name,
        "elapsed_time_s": act.elapsed_time_s,
        "moving_time_s": act.moving_time_s,
        "distance_m": act.distance_m,
        "elevation_gain_m": act.elevation_gain_m,
        "avg_power_w": act.avg_power_w,
        "weighted_avg_power_w": act.weighted_avg_power_w,
        "max_power_w": act.max_power_w,
        "avg_hr": act.avg_hr,
        "max_hr": act.max_hr,
        "avg_cadence": act.avg_cadence,
        "avg_speed_mps": act.avg_speed_mps,
        "kilojoules": act.kilojoules,
        "device_watts": act.device_watts,
        "trainer": act.trainer,
        "raw": act.raw,
    }
    stmt = insert(Activity).values(**values)
    _immutable = ("athlete_id", "provider", "provider_activity_id")
    update_cols = {k: stmt.excluded[k] for k in values if k not in _immutable}
    stmt = stmt.on_conflict_do_update(
        constraint="uq_provider_activity", set_=update_cols
    ).returning(Activity.id)
    return session.execute(stmt).scalar_one()


def upsert_stream(session: Session, activity_id: int, stream: CanonicalStream) -> None:
    stmt = insert(Stream).values(
        activity_id=activity_id,
        stream_type=stream.stream_type.value,
        data=stream.data,
        n_samples=stream.n_samples,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_activity_stream",
        set_={"data": stmt.excluded.data, "n_samples": stmt.excluded.n_samples},
    )
    session.execute(stmt)


def upsert_daily_metric(session: Session, athlete_id: int, m: CanonicalDailyMetric) -> None:
    stmt = insert(DailyMetric).values(
        athlete_id=athlete_id,
        day=m.day,
        metric=m.metric,
        value=m.value,
        source=m.source,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_athlete_day_metric",
        set_={"value": stmt.excluded.value, "source": stmt.excluded.source},
    )
    session.execute(stmt)


def activity_exists(session: Session, provider: str, provider_activity_id: str) -> bool:
    return session.execute(
        select(Activity.id).where(
            Activity.provider == provider,
            Activity.provider_activity_id == provider_activity_id,
        )
    ).first() is not None
