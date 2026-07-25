"""CLI del proyecto.  Uso:  `uv run cc <comando>`.

Comandos Fase 1:
  db-create    Crea el esquema en la BD (bootstrap de desarrollo).
  strava-auth  Autoriza la app contra Strava (OAuth) y guarda los tokens.
  backfill     Importa el histórico de actividades + streams.
  twin-show    Muestra el estado v0 del gemelo digital.
  stats        Cuenta lo que hay en la BD.
"""

from __future__ import annotations

import webbrowser
from datetime import UTC, datetime

import typer
from dateutil import parser as dateparser

from cycling_coach import accounts
from cycling_coach.adapters.strava.client import StravaClient
from cycling_coach.adapters.strava.oauth import (
    TokenSet,
    build_authorize_url,
    exchange_code,
)
from cycling_coach.adapters.strava.source import StravaSource
from cycling_coach.config import get_settings
from cycling_coach.db.engine import get_engine, session_scope
from cycling_coach.db.models import Activity, Athlete, Base, DailyMetric, Stream
from cycling_coach.ingest import backfill as run_backfill
from cycling_coach.oauth_loopback import wait_for_code
from cycling_coach.twin import build_state

app = typer.Typer(help="AI Cycling Coach — CLI de la Fase 1 (ingesta de datos).")


# --------------------------------------------------------------------------- #
@app.command("db-create")
def db_create() -> None:
    """Crea todas las tablas del esquema en la BD (bootstrap de desarrollo).

    Para producción/versionado real usaremos migraciones Alembic; esto sirve
    para arrancar rápido en local.
    """
    from sqlalchemy import text

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    typer.secho("Esquema creado ✔", fg=typer.colors.GREEN)


# --------------------------------------------------------------------------- #
@app.command("strava-auth")
def strava_auth() -> None:
    """Lanza el flujo OAuth de Strava y persiste los tokens."""
    settings = get_settings()
    if not settings.strava_client_id or not settings.strava_client_secret:
        typer.secho(
            "Faltan STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET en el .env.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    url = build_authorize_url(settings.strava_client_id, settings.strava_redirect_uri)
    typer.echo("Abriendo el navegador para autorizar en Strava...")
    typer.echo(f"Si no se abre, visita manualmente:\n  {url}\n")
    webbrowser.open(url)

    code = wait_for_code(settings.oauth_port)
    tokens = exchange_code(settings.strava_client_id, settings.strava_client_secret, code)

    with session_scope() as session:
        athlete = accounts.ensure_athlete(session)
        accounts.save_tokens(session, athlete.id, "strava", tokens)

    typer.secho(
        f"Autorizado ✔  atleta Strava id={tokens.athlete_id}, scope={tokens.scope}",
        fg=typer.colors.GREEN,
    )


# --------------------------------------------------------------------------- #
@app.command("backfill")
def backfill(
    since: str = typer.Option(
        "2010-01-01", help="Fecha inicial (YYYY-MM-DD) del histórico a importar."
    ),
    streams: bool = typer.Option(True, help="Descargar también los streams por actividad."),
    reprocess: bool = typer.Option(
        False, "--reprocess", help="Reprocesar actividades ya existentes."
    ),
) -> None:
    """Importa el histórico de Strava al modelo canónico."""
    settings = get_settings()
    after = dateparser.parse(since).replace(tzinfo=UTC)

    with session_scope() as session:
        loaded = accounts.load_tokens(session, "strava")
        if loaded is None:
            typer.secho(
                "No hay credenciales de Strava. Ejecuta `cc strava-auth`.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)
        account, tokens = loaded
        athlete_id = account.athlete_id

    def persist_refresh(new: TokenSet) -> None:
        with session_scope() as s:
            accounts.save_tokens(s, athlete_id, "strava", new)

    counter = {"n": 0}

    def on_progress(act, action: str) -> None:  # noqa: ANN001
        counter["n"] += 1
        mark = "·" if action == "skip" else "+"
        label = act.name or act.sport.value
        typer.echo(f"  {mark} [{counter['n']:>4}] {act.start_time:%Y-%m-%d}  {label}")

    with StravaClient(
        settings.strava_client_id, settings.strava_client_secret, tokens, persist_refresh
    ) as client:
        source = StravaSource(client)
        result = run_backfill(
            source,
            athlete_id,
            after=after,
            fetch_streams=streams,
            skip_existing=not reprocess,
            on_progress=on_progress,
        )

    typer.secho(
        f"\nHecho ✔  vistas={result.activities_seen}  "
        f"nuevas={result.activities_ingested}  "
        f"streams={result.streams_ingested}  "
        f"ya_existentes={result.skipped_existing}",
        fg=typer.colors.GREEN,
    )


# --------------------------------------------------------------------------- #
@app.command("twin-show")
def twin_show(
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
    as_of: str = typer.Option(None, help="Fecha de corte YYYY-MM-DD (por defecto, hoy)."),
) -> None:
    """Muestra el estado v0 del gemelo (capas static + daily)."""
    when = dateparser.parse(as_of).replace(tzinfo=UTC) if as_of else datetime.now(UTC)
    with session_scope() as session:
        if athlete_id is None:
            first = session.query(Athlete).order_by(Athlete.id).first()
            if first is None:
                typer.secho("No hay atletas en la BD.", fg=typer.colors.RED)
                raise typer.Exit(code=1)
            athlete_id = first.id
        state = build_state(session, athlete_id, when)

    typer.secho(f"AthleteState @ {state.as_of:%Y-%m-%d %H:%M UTC}", fg=typer.colors.CYAN, bold=True)
    typer.echo("  static:")
    for k, v in state.static.items():
        typer.echo(f"    {k:14} = {v}")
    typer.echo("  daily:")
    if not state.daily:
        typer.echo("    (sin métricas diarias aún)")
    for k, v in state.daily.items():
        typer.echo(f"    {k:14} = {v}")


# --------------------------------------------------------------------------- #
@app.command("stats")
def stats() -> None:
    """Cuenta filas de las entidades principales."""
    with session_scope() as session:
        n_ath = session.query(Athlete).count()
        n_act = session.query(Activity).count()
        n_str = session.query(Stream).count()
        n_day = session.query(DailyMetric).count()
    typer.echo(f"athletes={n_ath}  activities={n_act}  streams={n_str}  daily_metrics={n_day}")


if __name__ == "__main__":
    app()
