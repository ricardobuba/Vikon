"""Servicio de cuentas de proveedor: puente entre el `TokenSet` de OAuth y la
tabla `provider_account`, y alta del atleta local."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from cycling_coach.adapters.strava.oauth import TokenSet
from cycling_coach.db.models import Athlete, ProviderAccount
from cycling_coach.db.repositories import (
    clear_activity_raw_payloads,
    delete_streams_for_athlete,
)


def ensure_athlete(session: Session, name: str | None = None) -> Athlete:
    """Devuelve el primer atleta (dev N-of-1) o crea uno nuevo."""
    athlete = session.execute(select(Athlete).order_by(Athlete.id).limit(1)).scalar_one_or_none()
    if athlete is None:
        athlete = Athlete(name=name)
        session.add(athlete)
        session.flush()
    return athlete


def update_static_profile(
    session: Session, athlete_id: int, profile: dict, *, overwrite: bool = False
) -> dict:
    """Aplica un perfil-semilla (p. ej. de Strava) a la fila `athlete`.

    Por defecto solo rellena campos que estén vacíos (None), respetando lo que
    el usuario haya podido editar en la app. Con `overwrite=True` pisa el valor
    con el de la semilla. Devuelve el dict de campos efectivamente cambiados.
    """
    athlete = session.get(Athlete, athlete_id)
    if athlete is None:
        raise ValueError(f"No existe el atleta con id={athlete_id}")

    changed: dict = {}
    for field, value in profile.items():
        if not hasattr(athlete, field):
            continue
        current = getattr(athlete, field)
        if overwrite or current is None:
            if current != value:
                setattr(athlete, field, value)
                changed[field] = value
    session.flush()
    return changed


def save_tokens(
    session: Session, athlete_id: int, provider: str, tokens: TokenSet
) -> ProviderAccount:
    account = session.execute(
        select(ProviderAccount).where(
            ProviderAccount.provider == provider,
            ProviderAccount.provider_athlete_id == tokens.athlete_id,
        )
    ).scalar_one_or_none()

    if account is None:
        account = ProviderAccount(
            athlete_id=athlete_id,
            provider=provider,
            provider_athlete_id=tokens.athlete_id or "",
        )
        session.add(account)

    account.access_token = tokens.access_token
    account.refresh_token = tokens.refresh_token
    account.expires_at = tokens.expires_at
    account.scope = tokens.scope
    session.flush()
    return account


def _tokens_of(account: ProviderAccount) -> TokenSet:
    return TokenSet(
        access_token=account.access_token,
        refresh_token=account.refresh_token,
        expires_at=account.expires_at,  # type: ignore[arg-type]
        athlete_id=account.provider_athlete_id,
        scope=account.scope,
    )


def load_tokens(
    session: Session, provider: str, athlete_id: int | None = None
) -> tuple[ProviderAccount, TokenSet] | None:
    """Cuenta del proveedor. Con `athlete_id`, la DE ESE atleta.

    Sin `athlete_id` devuelve la primera (compatibilidad con el CLI de un solo
    usuario). En multi-perfil hay que pasarlo SIEMPRE: si no, el padre acabaría
    sincronizando el Strava del hijo."""
    q = select(ProviderAccount).where(ProviderAccount.provider == provider)
    if athlete_id is not None:
        q = q.where(ProviderAccount.athlete_id == athlete_id)
    account = session.execute(q.order_by(ProviderAccount.id)).scalars().first()
    if account is None:
        return None
    return account, _tokens_of(account)


def list_accounts(session: Session, provider: str) -> list[tuple[int, TokenSet]]:
    """(athlete_id, tokens) de TODAS las cuentas conectadas — para el bucle de
    sincronización en segundo plano, que ahora atiende a varios perfiles."""
    accounts = session.execute(
        select(ProviderAccount)
        .where(ProviderAccount.provider == provider)
        .order_by(ProviderAccount.id)
    ).scalars().all()
    return [(a.athlete_id, _tokens_of(a)) for a in accounts]


def delete_account(session: Session, athlete_id: int, provider: str) -> bool:
    """Quita la conexión con el proveedor de ese atleta. NO borra actividades:
    el historial importado es del atleta, no de la conexión."""
    account = session.execute(
        select(ProviderAccount).where(
            ProviderAccount.athlete_id == athlete_id,
            ProviderAccount.provider == provider,
        )
    ).scalars().first()
    if account is None:
        return False
    session.delete(account)
    session.flush()
    return True


def purge_raw_strava_data(session: Session, athlete_id: int) -> dict[str, int]:
    """Purga el material CRUDO de Strava del atleta (streams + JSON crudo de
    actividad) al desconectar la cuenta — cumple el borrado que exige la API
    Policy de Strava (reflejarlo en <=48h). Las columnas tipadas de `activity`
    (potencia media, TSS...) se CONSERVAN a propósito: son el historial de
    rendimiento del propio atleta y el motor de CTL/ATL/TSB las necesita.
    Ver BLINDAJE_LEGAL_Plan.md #3."""
    streams = delete_streams_for_athlete(session, athlete_id)
    activities = clear_activity_raw_payloads(session, athlete_id)
    session.flush()
    return {"streams_deleted": streams, "activities_cleared": activities}


def _serializable(value: object) -> object:
    """Convierte a algo que `json.dumps` acepte, sin perder precisión."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _rows_as_dicts(session: Session, model: type, *filters: object) -> list[dict]:
    rows = session.execute(select(model).where(*filters)).scalars().all()
    return [
        {
            col.name: _serializable(getattr(row, col.name))
            for col in model.__table__.columns
        }
        for row in rows
    ]


# Columnas que NUNCA salen en la exportación: son credenciales (secretos del
# sistema o de terceros), no datos personales del interesado. Entregarlas sería
# un agujero de seguridad disfrazado de derecho de acceso (RGPD art. 15.4:
# el derecho de acceso no puede afectar negativamente a derechos de terceros).
_EXPORT_REDACTED = {
    "pw_hash", "pw_salt", "access_token", "refresh_token",
}


def export_all_data(session: Session, athlete_id: int) -> dict:
    """Todos los datos personales del atleta, en un dict serializable a JSON.

    Cubre los derechos de acceso (art. 15) y portabilidad (art. 20): formato
    estructurado, de uso común y lectura mecánica. Se omiten las credenciales
    (contraseña y tokens de Strava), que no son datos suyos sino secretos."""
    from cycling_coach.db.models import (
        Activity,
        ActivityMmp,
        Athlete,
        Availability,
        AvailabilityOverride,
        ChatMessage,
        DailyMetric,
        Goal,
        ModelConfig,
        ParameterEstimate,
        PlanLog,
        PlanOverride,
        ProviderAccount,
        Stream,
        TestResult,
        User,
    )

    by_athlete: dict[str, type] = {
        "athlete": Athlete,
        "provider_account": ProviderAccount,
        "activity": Activity,
        "activity_mmp": ActivityMmp,
        "test_result": TestResult,
        "model_config": ModelConfig,
        "parameter_estimate": ParameterEstimate,
        "goal": Goal,
        "availability": Availability,
        "availability_override": AvailabilityOverride,
        "plan_override": PlanOverride,
        "plan_log": PlanLog,
        "chat_message": ChatMessage,
        "daily_metric": DailyMetric,
        "app_user": User,
    }

    data: dict = {}
    for name, model in by_athlete.items():
        key = Athlete.id if model is Athlete else model.athlete_id
        data[name] = _rows_as_dicts(session, model, key == athlete_id)

    # `stream` cuelga de la actividad, no del atleta.
    data["stream"] = _rows_as_dicts(
        session,
        Stream,
        Stream.activity_id.in_(
            select(Activity.id).where(Activity.athlete_id == athlete_id)
        ),
    )

    for rows in data.values():
        for row in rows:
            for field in _EXPORT_REDACTED & row.keys():
                row[field] = "[omitido: credencial]"

    return {
        "exportado_el": datetime.now(UTC).isoformat(),
        "athlete_id": athlete_id,
        "aviso": (
            "Exportación completa de tus datos personales en Vikon (RGPD arts. "
            "15 y 20). Se omiten contraseña y tokens de Strava por seguridad."
        ),
        "datos": data,
    }


def delete_athlete_and_user(session: Session, athlete_id: int) -> None:
    """Borra el atleta y TODO lo que cuelga de él (RGPD art. 17).

    Usa una sentencia DELETE, no `session.delete(obj)`, a propósito: solo
    `accounts` y `activities` están declaradas con `cascade="all, delete-orphan"`
    en el ORM; el resto de tablas dependen del `ON DELETE CASCADE` de la base de
    datos, que la cascada en memoria de SQLAlchemy no dispara. Con `session.delete`
    creerías haber borrado y quedarían filas huérfanas."""
    from cycling_coach.db.models import Athlete

    session.execute(delete(Athlete).where(Athlete.id == athlete_id))
    session.flush()


def account_owner(
    session: Session, provider: str, provider_athlete_id: str
) -> int | None:
    """Atleta local dueño de esa cuenta del proveedor, si ya está enlazada.
    Sirve para no dejar que dos perfiles reclamen el mismo Strava."""
    account = session.execute(
        select(ProviderAccount).where(
            ProviderAccount.provider == provider,
            ProviderAccount.provider_athlete_id == provider_athlete_id,
        )
    ).scalar_one_or_none()
    return account.athlete_id if account else None
