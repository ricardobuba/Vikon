"""Servicio de cuentas de proveedor: puente entre el `TokenSet` de OAuth y la
tabla `provider_account`, y alta del atleta local."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from cycling_coach.adapters.strava.oauth import TokenSet
from cycling_coach.db.models import Athlete, ProviderAccount


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


def load_tokens(session: Session, provider: str) -> tuple[ProviderAccount, TokenSet] | None:
    account = session.execute(
        select(ProviderAccount)
        .where(ProviderAccount.provider == provider)
        .order_by(ProviderAccount.id)
    ).scalars().first()
    if account is None:
        return None
    tokens = TokenSet(
        access_token=account.access_token,
        refresh_token=account.refresh_token,
        expires_at=account.expires_at,  # type: ignore[arg-type]
        athlete_id=account.provider_athlete_id,
        scope=account.scope,
    )
    return account, tokens
