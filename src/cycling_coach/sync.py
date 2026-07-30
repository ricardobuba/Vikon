"""Sincronización incremental con Strava: trae SOLO lo nuevo desde la última
actividad en BD. Rápida (pocas llamadas) → apta para lanzarse automáticamente
al abrir la app o periódicamente, no solo en un backfill manual.

El plan calcula TSB/CTL en vivo desde la tabla de actividades, así que ingerir
la salida de hoy basta para que el plan la refleje (el FTP/CP se recalcula
aparte con `cc estimate-cp`, cambia despacio)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from cycling_coach import accounts
from cycling_coach.adapters.strava.client import StravaClient
from cycling_coach.adapters.strava.oauth import TokenSet
from cycling_coach.adapters.strava.source import StravaSource
from cycling_coach.config import get_settings
from cycling_coach.db.engine import session_scope
from cycling_coach.db.models import Activity
from cycling_coach.ingest import BackfillResult, backfill


class SyncError(RuntimeError):
    """No se pudo sincronizar (sin credenciales, o Strava no responde)."""


def _last_activity_after(athlete_id: int, overlap_days: int, default_days: int) -> datetime:
    """Fecha desde la que pedir: última actividad − solape (o `default_days` si
    no hay historial). El solape recupara ediciones/subidas tardías."""
    with session_scope() as session:
        last = session.execute(
            select(func.max(Activity.start_time)).where(Activity.athlete_id == athlete_id)
        ).scalar_one_or_none()
    if last is None:
        return datetime.now(UTC) - timedelta(days=default_days)
    return last - timedelta(days=overlap_days)


def sync_recent(
    *,
    athlete_id: int | None = None,
    overlap_days: int = 3,
    default_days: int = 45,
    fetch_streams: bool = True,
) -> BackfillResult:
    """Ingesta incremental de las actividades nuevas DE UN ATLETA. Devuelve el
    resultado (nuevas, streams, ya existentes). Lanza SyncError si falta
    autorización.

    `athlete_id` es obligatorio de facto en multi-perfil: sin él se coge la
    primera cuenta (compatibilidad con el CLI de un solo usuario), y con varios
    perfiles eso sincronizaría la cuenta equivocada."""
    settings = get_settings()
    with session_scope() as session:
        loaded = accounts.load_tokens(session, "strava", athlete_id)
        if loaded is None:
            raise SyncError(
                "Este perfil no tiene Strava conectado."
                if athlete_id is not None
                else "Sin credenciales de Strava. Ejecuta `cc strava-auth`."
            )
        account, tokens = loaded
        athlete_id = account.athlete_id

    after = _last_activity_after(athlete_id, overlap_days, default_days)

    def persist_refresh(new: TokenSet) -> None:
        with session_scope() as s:
            accounts.save_tokens(s, athlete_id, "strava", new)

    try:
        with StravaClient(
            settings.strava_client_id, settings.strava_client_secret, tokens, persist_refresh
        ) as client:
            return backfill(
                StravaSource(client),
                athlete_id,
                after=after,
                fetch_streams=fetch_streams,
                skip_existing=True,
            )
    except Exception as exc:  # red / API / token — no tumbar la app por esto
        raise SyncError(f"No pude sincronizar con Strava: {exc}") from exc


def sync_all(*, fetch_streams: bool = False) -> dict[int, BackfillResult | SyncError]:
    """Sincroniza TODOS los perfiles conectados. Para el bucle en segundo plano:
    con varios perfiles no basta con atender a una cuenta.

    Un fallo en un perfil no impide sincronizar los demás — se devuelve por
    atleta para que el llamador lo registre."""
    with session_scope() as session:
        athlete_ids = [aid for aid, _ in accounts.list_accounts(session, "strava")]
    out: dict[int, BackfillResult | SyncError] = {}
    for aid in athlete_ids:
        try:
            out[aid] = sync_recent(athlete_id=aid, fetch_streams=fetch_streams)
        except SyncError as exc:
            out[aid] = exc
    return out
