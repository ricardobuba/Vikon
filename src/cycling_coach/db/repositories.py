"""Operaciones de persistencia idempotentes (upsert) para la ingesta.

El backfill puede reejecutarse sin duplicar: usamos ON CONFLICT sobre las
restricciones únicas naturales (provider_activity_id, etc.).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cycling_coach.db.models import (
    Activity,
    AppMeta,
    Athlete,
    Availability,
    AvailabilityOverride,
    ChatMessage,
    DailyMetric,
    Goal,
    ModelConfig,
    ParameterEstimate,
    Stream,
    TestResult,
    User,
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


def load_activity_loads(
    session: Session, athlete_id: int
) -> list[tuple[date, int, float]]:
    """(día, duración_s, NP_w) por actividad con potencia, para calcular TSS.
    NP = weighted_avg_power_w (NP de Strava) o, en su defecto, avg_power_w."""
    rows = session.execute(
        select(
            Activity.start_time,
            Activity.moving_time_s,
            Activity.elapsed_time_s,
            Activity.weighted_avg_power_w,
            Activity.avg_power_w,
        )
        .where(Activity.athlete_id == athlete_id)
        .order_by(Activity.start_time)
    ).all()
    out: list[tuple[date, int, float]] = []
    for start, moving, elapsed, wap, avg in rows:
        np_w = wap if wap is not None else avg
        dur = moving if moving is not None else elapsed
        if np_w and dur:
            out.append((start.date(), int(dur), float(np_w)))
    return out


def latest_daily_metric(
    session: Session, athlete_id: int, metric: str, on_or_before: date | None = None
) -> tuple[date, float] | None:
    """Último valor (día, valor) de una métrica diaria, opcionalmente <= fecha."""
    q = select(DailyMetric.day, DailyMetric.value).where(
        DailyMetric.athlete_id == athlete_id, DailyMetric.metric == metric
    )
    if on_or_before is not None:
        q = q.where(DailyMetric.day <= on_or_before)
    row = session.execute(q.order_by(DailyMetric.day.desc()).limit(1)).first()
    return (row[0], row[1]) if row else None


def load_hr_only_loads(
    session: Session, athlete_id: int
) -> list[tuple[date, int, float]]:
    """(día, duración_s, HR media) de actividades CON pulso pero SIN potencia
    (para carga por TRIMP sin duplicar las que ya tienen potencia)."""
    rows = session.execute(
        select(
            Activity.start_time,
            Activity.moving_time_s,
            Activity.elapsed_time_s,
            Activity.avg_hr,
        )
        .where(
            Activity.athlete_id == athlete_id,
            Activity.avg_hr.is_not(None),
            Activity.weighted_avg_power_w.is_(None),
            Activity.avg_power_w.is_(None),
        )
        .order_by(Activity.start_time)
    ).all()
    out: list[tuple[date, int, float]] = []
    for start, moving, elapsed, hr in rows:
        dur = moving if moving is not None else elapsed
        if hr and dur:
            out.append((start.date(), int(dur), float(hr)))
    return out


def estimate_hr_bounds(
    session: Session, athlete_id: int, default_rest: float = 55.0
) -> tuple[float, float] | None:
    """(HRrep, HRmax) estimados: HRmax del máximo observado; HRrep del último
    resting_hr o un valor por defecto. None si no hay HR."""
    hr_max = session.execute(
        select(func.max(Activity.max_hr)).where(Activity.athlete_id == athlete_id)
    ).scalar_one_or_none()
    if not hr_max:
        return None
    rest_row = latest_daily_metric(session, athlete_id, "resting_hr")
    hr_rest = rest_row[1] if rest_row else default_rest
    return float(hr_rest), float(hr_max)


def latest_parameter_estimate(
    session: Session, athlete_id: int, param: str
) -> float | None:
    return session.execute(
        select(ParameterEstimate.mean)
        .where(
            ParameterEstimate.athlete_id == athlete_id,
            ParameterEstimate.param == param,
        )
        .order_by(ParameterEstimate.as_of.desc())
        .limit(1)
    ).scalar_one_or_none()


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


def load_watts_stream(session: Session, activity_id: int) -> list | None:
    """Serie de potencia (1 Hz) de una actividad. None si no la tiene."""
    return session.execute(
        select(Stream.data).where(
            Stream.activity_id == activity_id, Stream.stream_type == "watts"
        )
    ).scalar_one_or_none()


def get_activity(session: Session, athlete_id: int, activity_id: int) -> Activity | None:
    return session.execute(
        select(Activity).where(
            Activity.id == activity_id, Activity.athlete_id == athlete_id
        )
    ).scalar_one_or_none()


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


def save_model_config(session: Session, athlete_id: int, config: dict) -> None:
    stmt = insert(ModelConfig).values(athlete_id=athlete_id, config=config)
    stmt = stmt.on_conflict_do_update(
        index_elements=[ModelConfig.athlete_id], set_={"config": stmt.excluded.config}
    )
    session.execute(stmt)


def load_model_config(session: Session, athlete_id: int) -> dict | None:
    return session.execute(
        select(ModelConfig.config).where(ModelConfig.athlete_id == athlete_id)
    ).scalar_one_or_none()


def save_cri_weights(session: Session, athlete_id: int, weights: dict | None) -> None:
    stmt = insert(ModelConfig).values(
        athlete_id=athlete_id, config={}, cri_weights=weights
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ModelConfig.athlete_id],
        set_={"cri_weights": stmt.excluded.cri_weights},
    )
    session.execute(stmt)


def load_cri_weights(session: Session, athlete_id: int) -> dict | None:
    return session.execute(
        select(ModelConfig.cri_weights).where(ModelConfig.athlete_id == athlete_id)
    ).scalar_one_or_none()


def mark_activity_as_test(session: Session, activity_id: int) -> Activity | None:
    """Marca una actividad como esfuerzo maximal. Devuelve la actividad o None."""
    act = session.get(Activity, activity_id)
    if act is None:
        return None
    act.is_maximal_test = True
    session.flush()
    return act


def find_activity_on_date(
    session: Session, athlete_id: int, day: date
) -> Activity | None:
    """Actividad con potencia real de ese día (la de mayor potencia media)."""
    return session.execute(
        select(Activity)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.device_watts.is_(True),
            func.date(Activity.start_time) == day,
        )
        .order_by(Activity.avg_power_w.desc().nullslast())
        .limit(1)
    ).scalar_one_or_none()


def latest_power_activity(session: Session, athlete_id: int) -> Activity | None:
    return session.execute(
        select(Activity)
        .where(Activity.athlete_id == athlete_id, Activity.device_watts.is_(True))
        .order_by(Activity.start_time.desc())
        .limit(1)
    ).scalar_one_or_none()


def load_marked_test_activities(
    session: Session, athlete_id: int
) -> list[tuple[datetime, int, list]]:
    """(fecha, id, watts) de las actividades marcadas como test maximal."""
    rows = session.execute(
        select(Activity.start_time, Activity.id, Stream.data)
        .join(Stream, Stream.activity_id == Activity.id)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.is_maximal_test.is_(True),
            Stream.stream_type == "watts",
        )
        .order_by(Activity.start_time)
    ).all()
    return [(start, aid, data) for start, aid, data in rows]


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


def add_goal(
    session: Session,
    athlete_id: int,
    event_date: date,
    name: str | None = None,
    kind: str | None = None,
    priority: str = "A",
) -> Goal:
    """Registra (o ACTUALIZA) el evento objetivo de esa fecha.

    Upsert por (atleta, fecha): guardar el perfil dos veces no debe duplicar el
    objetivo — antes se acumulaban y el planner podía quedarse con una versión
    vieja sin `kind`, ignorando el tipo de evento."""
    goal = session.execute(
        select(Goal).where(Goal.athlete_id == athlete_id, Goal.event_date == event_date)
        .order_by(Goal.id.desc()).limit(1)
    ).scalar_one_or_none()
    if goal is None:
        goal = Goal(athlete_id=athlete_id, event_date=event_date)
        session.add(goal)
    if name is not None:
        goal.name = name
    if kind is not None:
        goal.kind = kind
    goal.priority = priority
    session.flush()
    return goal


def next_goal(session: Session, athlete_id: int, on_or_after: date) -> Goal | None:
    """Próximo evento futuro (el más cercano; a igualdad, mayor prioridad A<B<C).

    Último desempate: el registrado MÁS RECIENTEMENTE (id desc) — si hay
    duplicados antiguos de la misma fecha, manda el último que guardaste (que es
    el que lleva el tipo de evento actualizado)."""
    return session.execute(
        select(Goal)
        .where(Goal.athlete_id == athlete_id, Goal.event_date >= on_or_after)
        .order_by(Goal.event_date.asc(), Goal.priority.asc(), Goal.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def list_goals(session: Session, athlete_id: int) -> list[Goal]:
    return list(
        session.execute(
            select(Goal)
            .where(Goal.athlete_id == athlete_id)
            .order_by(Goal.event_date)
        ).scalars().all()
    )


def training_seconds_on(session: Session, athlete_id: int, day: date) -> int:
    """Segundos de entrenamiento (moving, o elapsed en su defecto) de ese día.
    Sirve para saber si el atleta YA ha entrenado hoy."""
    total = session.execute(
        select(
            func.coalesce(
                func.sum(func.coalesce(Activity.moving_time_s, Activity.elapsed_time_s)), 0
            )
        ).where(
            Activity.athlete_id == athlete_id,
            func.date(Activity.start_time) == day,
        )
    ).scalar_one()
    return int(total or 0)


# --- Perfil y disponibilidad (onboarding) ------------------------------------
_PROFILE_FIELDS = (
    "name", "sex", "birthdate", "height_cm", "weight_kg", "level",
    "declared_ftp_w", "hr_max", "hr_rest", "weekly_minutes_target",
)


def get_athlete(session: Session, athlete_id: int) -> Athlete | None:
    return session.get(Athlete, athlete_id)


def save_profile(session: Session, athlete_id: int, data: dict) -> Athlete:
    """Actualiza los campos de perfil presentes en `data` y marca onboarded."""
    athlete = session.get(Athlete, athlete_id)
    if athlete is None:
        raise ValueError(f"atleta {athlete_id} no existe")
    for field in _PROFILE_FIELDS:
        if field in data:
            setattr(athlete, field, data[field])
    athlete.onboarded = True
    session.flush()
    return athlete


def get_availability(session: Session, athlete_id: int) -> dict[int, int]:
    """{weekday(0=lunes)→minutos}. Vacío si no se ha configurado."""
    rows = session.execute(
        select(Availability.weekday, Availability.minutes).where(
            Availability.athlete_id == athlete_id
        )
    ).all()
    return {wd: mins for wd, mins in rows}


def set_availability(session: Session, athlete_id: int, per_day: dict[int, int]) -> None:
    """Reemplaza la disponibilidad semanal (upsert por día)."""
    for weekday, minutes in per_day.items():
        stmt = insert(Availability).values(
            athlete_id=athlete_id, weekday=int(weekday), minutes=int(minutes)
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["athlete_id", "weekday"],
            set_={"minutes": int(minutes)},
        )
        session.execute(stmt)


# --- Cuentas / autenticación -------------------------------------------------
def count_users(session: Session) -> int:
    return session.execute(select(func.count()).select_from(User)).scalar_one()


def get_user(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()


def create_user(
    session: Session, username: str, pw_hash: str, pw_salt: str, athlete_id: int
) -> User:
    user = User(
        username=username, pw_hash=pw_hash, pw_salt=pw_salt, athlete_id=athlete_id
    )
    session.add(user)
    session.flush()
    return user


def first_athlete_id(session: Session) -> int | None:
    return session.execute(
        select(Athlete.id).order_by(Athlete.id).limit(1)
    ).scalar_one_or_none()


def create_athlete(session: Session, name: str | None = None) -> int:
    athlete = Athlete(name=name)
    session.add(athlete)
    session.flush()
    return athlete.id


def get_or_create_secret(session: Session) -> str:
    """Secreto para firmar las cookies de sesión (persistente en la BD)."""
    row = session.get(AppMeta, "auth_secret")
    if row is not None:
        return row.value
    import secrets

    value = secrets.token_hex(32)
    session.add(AppMeta(key="auth_secret", value=value))
    session.flush()
    return value


# --- Disponibilidad de días sueltos (excepciones puntuales) ------------------
def get_availability_overrides(
    session: Session, athlete_id: int, start: date, end: date
) -> dict[date, int]:
    """{fecha → minutos} de las excepciones puntuales en [start, end]."""
    rows = session.execute(
        select(AvailabilityOverride.day, AvailabilityOverride.minutes).where(
            AvailabilityOverride.athlete_id == athlete_id,
            AvailabilityOverride.day >= start,
            AvailabilityOverride.day <= end,
        )
    ).all()
    return {d: m for d, m in rows}


def set_availability_override(
    session: Session, athlete_id: int, day: date, minutes: int
) -> None:
    """Fija (o actualiza) los minutos disponibles de un día concreto."""
    stmt = insert(AvailabilityOverride).values(
        athlete_id=athlete_id, day=day, minutes=int(minutes)
    )
    session.execute(stmt.on_conflict_do_update(
        index_elements=["athlete_id", "day"], set_={"minutes": int(minutes)},
    ))


def clear_availability_override(session: Session, athlete_id: int, day: date) -> None:
    """Quita la excepción de ese día (vuelve a mandar la semanal)."""
    session.execute(
        delete(AvailabilityOverride).where(
            AvailabilityOverride.athlete_id == athlete_id,
            AvailabilityOverride.day == day,
        )
    )


# --- Memoria del chat (~1 semana) --------------------------------------------
def add_chat_messages(
    session: Session, athlete_id: int, messages: list[tuple[str, str]]
) -> None:
    """Guarda [(rol, texto)] en el historial."""
    for role, content in messages:
        session.add(ChatMessage(athlete_id=athlete_id, role=role, content=content))
    session.flush()


def recent_chat(
    session: Session, athlete_id: int, since: datetime, limit: int = 40
) -> list[dict[str, str]]:
    """Historial reciente en formato de mensajes del LLM (viejo→nuevo)."""
    rows = session.execute(
        select(ChatMessage)
        .where(ChatMessage.athlete_id == athlete_id, ChatMessage.created_at >= since)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


def purge_old_chat(session: Session, athlete_id: int, before: datetime) -> None:
    """Borra el historial anterior a `before` (retención de ~1 semana)."""
    session.execute(
        delete(ChatMessage).where(
            ChatMessage.athlete_id == athlete_id, ChatMessage.created_at < before
        )
    )


def activity_exists(session: Session, provider: str, provider_activity_id: str) -> bool:
    return session.execute(
        select(Activity.id).where(
            Activity.provider == provider,
            Activity.provider_activity_id == provider_activity_id,
        )
    ).first() is not None
