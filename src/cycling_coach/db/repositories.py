"""Operaciones de persistencia idempotentes (upsert) para la ingesta.

El backfill puede reejecutarse sin duplicar: usamos ON CONFLICT sobre las
restricciones únicas naturales (provider_activity_id, etc.).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cycling_coach.db.models import (
    Activity,
    DailyMetric,
    ParameterEstimate,
    Stream,
    TestResult,
)
from cycling_coach.domain.models import (
    CanonicalActivity,
    CanonicalDailyMetric,
    CanonicalStream,
    Estimate,
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


def load_power_activities(
    session: Session, athlete_id: int
) -> list[tuple[datetime, int, list]]:
    """(fecha, activity_id, stream_watts) de las actividades con potenciómetro
    real, ordenadas por fecha. Entrada del estimador de CP/W'."""
    rows = session.execute(
        select(Activity.start_time, Activity.id, Stream.data)
        .join(Stream, Stream.activity_id == Activity.id)
        .where(
            Activity.athlete_id == athlete_id,
            Stream.stream_type == "watts",
            Activity.device_watts.is_(True),
        )
        .order_by(Activity.start_time)
    ).all()
    return [(start, aid, data) for start, aid, data in rows]


def store_parameter_estimate(
    session: Session, athlete_id: int, param: str, est: Estimate
) -> None:
    """Añade (append-only) un posterior de un parámetro `slow` del gemelo."""
    session.add(
        ParameterEstimate(
            athlete_id=athlete_id,
            param=param,
            mean=est.mean,
            sd=est.sd,
            ci90_low=est.ci90[0],
            ci90_high=est.ci90[1],
            as_of=est.updated_at,
            source=est.source,
        )
    )


def store_test_result(
    session: Session,
    athlete_id: int,
    date: datetime,
    kind: str,
    cp: float,
    sd_cp: float,
    w_prime: float | None = None,
    sd_wp: float | None = None,
    notes: str | None = None,
) -> None:
    session.add(
        TestResult(
            athlete_id=athlete_id,
            date=date,
            kind=kind,
            cp=cp,
            sd_cp=sd_cp,
            w_prime=w_prime,
            sd_wp=sd_wp,
            notes=notes,
        )
    )


def load_test_results(session: Session, athlete_id: int) -> list[TestResult]:
    return list(
        session.execute(
            select(TestResult)
            .where(TestResult.athlete_id == athlete_id)
            .order_by(TestResult.date)
        ).scalars().all()
    )


def activity_exists(session: Session, provider: str, provider_activity_id: str) -> bool:
    return session.execute(
        select(Activity.id).where(
            Activity.provider == provider,
            Activity.provider_activity_id == provider_activity_id,
        )
    ).first() is not None
