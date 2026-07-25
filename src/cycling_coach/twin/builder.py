"""Construcción del `AthleteState` v0 (cap. 3.4, Fase 1).

Fase 1 puebla dos capas:
  - `static`: variables permanentes desde la fila `athlete`.
  - `daily` : último valor conocido de cada métrica diaria (<= as_of).

`slow` (FTP, CP, W'...) y `latent` (variables ocultas) quedan vacías: se activan
en Fase 2 con la estimación bayesiana. La forma de datos ya está preparada para
ellas, así no hay que rehacer el gemelo (nota de diseño del roadmap).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cycling_coach.db.models import Athlete, DailyMetric
from cycling_coach.domain.models import AthleteState


def build_state(
    session: Session, athlete_id: int, as_of: datetime | None = None
) -> AthleteState:
    as_of = as_of or datetime.now(UTC)
    as_of_day: date = as_of.date()

    athlete = session.get(Athlete, athlete_id)
    if athlete is None:
        raise ValueError(f"No existe el atleta con id={athlete_id}")

    static: dict = {
        "name": athlete.name,
        "sex": athlete.sex,
        "birthdate": athlete.birthdate.isoformat() if athlete.birthdate else None,
        "height_cm": athlete.height_cm,
        "weight_kg": athlete.weight_kg,
        "experience": athlete.experience,
    }
    if athlete.birthdate is not None:
        static["age_years"] = _age_years(athlete.birthdate, as_of_day)

    daily = _latest_daily_metrics(session, athlete_id, as_of_day)

    return AthleteState(static=static, daily=daily, as_of=as_of)


def _latest_daily_metrics(
    session: Session, athlete_id: int, as_of_day: date
) -> dict[str, float]:
    """Para cada métrica, el valor del día más reciente <= as_of_day."""
    latest_day = (
        select(
            DailyMetric.metric,
            func.max(DailyMetric.day).label("day"),
        )
        .where(DailyMetric.athlete_id == athlete_id, DailyMetric.day <= as_of_day)
        .group_by(DailyMetric.metric)
        .subquery()
    )
    rows = session.execute(
        select(DailyMetric.metric, DailyMetric.value).join(
            latest_day,
            (DailyMetric.metric == latest_day.c.metric)
            & (DailyMetric.day == latest_day.c.day),
        ).where(DailyMetric.athlete_id == athlete_id)
    ).all()
    return {metric: value for metric, value in rows}


def _age_years(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
