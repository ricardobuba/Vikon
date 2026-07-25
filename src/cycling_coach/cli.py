"""CLI del proyecto.  Uso:  `uv run cc <comando>`.

Comandos Fase 1:
  db-create    Crea el esquema en la BD (bootstrap de desarrollo).
  strava-auth  Autoriza la app contra Strava (OAuth) y guarda los tokens.
  backfill     Importa el histórico de actividades + streams.
  twin-show    Muestra el estado v0 del gemelo digital.
  stats        Cuenta lo que hay en la BD.
"""

from __future__ import annotations

import sys
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
from cycling_coach.db.repositories import (
    find_activity_on_date,
    latest_power_activity,
    mark_activity_as_test,
    store_parameter_estimate,
    store_test_result,
)
from cycling_coach.domain.models import Estimate
from cycling_coach.ingest import backfill as run_backfill
from cycling_coach.oauth_loopback import wait_for_code
from cycling_coach.twin import build_state
from cycling_coach.twin import estimate_cp as estimate_cp_service
from cycling_coach.twin.cp_estimation import CPEstimationResult
from cycling_coach.twin.cp_estimation import backtest as backtest_service
from cycling_coach.twin.cp_estimation import tune as tune_service

# La consola de Windows usa cp1252 por defecto y revienta al imprimir glifos
# como ✔ o ·. Forzamos UTF-8 en la salida para que la CLI sea portable.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

app = typer.Typer(help="AI Cycling Coach — CLI de la Fase 1 (ingesta de datos).")


# --------------------------------------------------------------------------- #
@app.command("db-create")
def db_create() -> None:
    """Crea todas las tablas del esquema en la BD (bootstrap de desarrollo).

    Para producción/versionado real usaremos migraciones Alembic; esto sirve
    para arrancar rápido en local.

    pgvector es opcional en Fase 1 (solo se usa en la fase del RAG científico):
    si la extensión no está disponible, se avisa y se continúa igualmente.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        typer.echo("  pgvector habilitado.")
    except SQLAlchemyError:
        typer.secho(
            "  pgvector no disponible (se añadirá en la fase del RAG). Continúo sin él.",
            fg=typer.colors.YELLOW,
        )
    Base.metadata.create_all(engine)
    # create_all no altera tablas existentes; añadimos columnas nuevas a mano
    # (idempotente). Para producción, migraciones Alembic.
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE activity ADD COLUMN IF NOT EXISTS "
                "is_maximal_test boolean NOT NULL DEFAULT false"
            )
        )
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
@app.command("athlete-sync")
def athlete_sync(
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Pisar valores existentes con los de Strava."
    ),
) -> None:
    """Trae el perfil estático desde Strava (nombre, sexo, peso) al gemelo.

    Es una SEMILLA: por defecto solo rellena campos vacíos, respetando lo que
    edites en la app. Strava no expone fecha de nacimiento ni altura.
    """
    settings = get_settings()
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

    with StravaClient(
        settings.strava_client_id, settings.strava_client_secret, tokens, persist_refresh
    ) as client:
        profile = StravaSource(client).get_athlete_profile()

    with session_scope() as session:
        changed = accounts.update_static_profile(
            session, athlete_id, profile, overwrite=overwrite
        )

    typer.secho(f"Perfil de Strava: {profile}", fg=typer.colors.CYAN)
    if changed:
        typer.secho(f"Actualizado ✔  {changed}", fg=typer.colors.GREEN)
    else:
        typer.secho(
            "Nada que actualizar (ya estaba relleno; usa --overwrite para forzar).",
            fg=typer.colors.YELLOW,
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
        f"ya_existentes={result.skipped_existing}  "
        f"errores_stream={result.stream_errors}",
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
    typer.echo("  slow (estimado, con incertidumbre):")
    if not state.slow:
        typer.echo("    (sin estimaciones aún; ejecuta `cc estimate-cp`)")
    for k, est in state.slow.items():
        typer.echo(
            f"    {k:14} = {est.mean:.0f} (90% CI {est.ci90[0]:.0f}–{est.ci90[1]:.0f})"
            f"  [{est.source}]"
        )


def _resolve_athlete_id(session, athlete_id: int | None) -> int:
    if athlete_id is not None:
        return athlete_id
    first = session.query(Athlete).order_by(Athlete.id).first()
    if first is None:
        typer.secho("No hay atletas en la BD.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    return first.id


def _honest_estimate(mean: float, sd: float, as_of, source: str) -> Estimate:
    return Estimate(
        mean=mean,
        sd=sd,
        ci90=(mean - 1.645 * sd, mean + 1.645 * sd),
        updated_at=as_of,
        source=source,
    )


def _report_and_persist(
    athlete_id: int, result: CPEstimationResult, store: bool
) -> None:
    cur, rec = result.state, result.recommendation
    sd = result.predictive_sd_cp   # incertidumbre honesta (error demostrado)
    cp_est = _honest_estimate(cur.cp.mean, sd, cur.as_of, cur.cp.source)
    ftp_est = _honest_estimate(cur.ftp_w, sd, cur.as_of, cur.cp.source)

    typer.secho(f"Estimación @ {cur.as_of:%Y-%m-%d}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  CP  = {cp_est.mean:.0f} W  (90% CI {cp_est.ci90[0]:.0f}–{cp_est.ci90[1]:.0f})")
    typer.echo(
        f"  FTP = {ftp_est.mean:.0f} W  "
        f"(90% CI {ftp_est.ci90[0]:.0f}–{ftp_est.ci90[1]:.0f})"
    )
    typer.echo(f"  W'  = {cur.w_prime.mean / 1000:.1f} kJ")
    typer.echo(f"  (obs: {result.n_activity_obs} de actividades, {result.n_test_obs} de tests)")
    color = typer.colors.YELLOW if rec.recommended else typer.colors.GREEN
    verdict = "RECOMENDADO" if rec.recommended else "no necesario"
    typer.secho(f"  Test: {verdict} — {rec.reason}", fg=color)

    if store:
        with session_scope() as session:
            store_parameter_estimate(session, athlete_id, "cp", cp_est)
            store_parameter_estimate(session, athlete_id, "w_prime", cur.w_prime)
            store_parameter_estimate(session, athlete_id, "ftp", ftp_est)
        typer.secho("Persistido en parameter_estimate ✔", fg=typer.colors.GREEN)


# --------------------------------------------------------------------------- #
@app.command("estimate-cp")
def estimate_cp(
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
    store: bool = typer.Option(True, help="Guardar el posterior en parameter_estimate."),
) -> None:
    """Estima CP/W'/FTP actuales (filtro bayesiano, actividades + tests)."""
    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        result = estimate_cp_service(session, athlete_id)
    if result is None:
        typer.secho(
            "Sin datos suficientes. Ejecuta `cc backfill` o añade un test con `cc add-test`.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)
    _report_and_persist(athlete_id, result, store)


# --------------------------------------------------------------------------- #
@app.command("add-test")
def add_test(
    ftp: float = typer.Option(None, help="FTP medido (W). P. ej. de un test."),
    cp: float = typer.Option(None, help="CP medido directamente (W)."),
    wprime: float = typer.Option(None, help="W' medido (kJ), junto con --cp."),
    minutes: float = typer.Option(None, help="Duración de un esfuerzo maximal (min)."),
    watts: float = typer.Option(None, help="Potencia media de ese esfuerzo (W)."),
    ramp_max: float = typer.Option(None, help="Potencia máx de 1 min en un test de rampa (W)."),
    date: str = typer.Option(None, help="Fecha del test YYYY-MM-DD (por defecto, hoy)."),
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
) -> None:
    """Registra un test de campo como ancla de alta confianza y re-estima.

    Modos (elige uno):
      --ftp 350                      FTP medido
      --cp 355 [--wprime 22]         CP (y W') directos
      --minutes 20 --watts 370       un esfuerzo maximal (mejor >= 12 min)
      --ramp-max 460                 test de rampa (potencia máx de 1 min)
    """
    ftp_ratio = 0.99
    when = dateparser.parse(date).replace(tzinfo=UTC) if date else datetime.now(UTC)

    kind: str
    cp_val: float
    sd_cp: float
    wp_val: float | None = None
    sd_wp: float | None = None
    notes: str | None = None

    if cp is not None:
        kind, cp_val, sd_cp = "cp", cp, 5.0
        if wprime is not None:
            wp_val, sd_wp = wprime * 1000.0, 2000.0
        notes = "CP/W' directos"
    elif ftp is not None:
        kind, cp_val, sd_cp = "ftp", ftp / ftp_ratio, 6.0
        notes = f"FTP medido {ftp:.0f} W"
    elif ramp_max is not None:
        ftp_est = 0.75 * ramp_max
        kind, cp_val, sd_cp = "ramp", ftp_est / ftp_ratio, 10.0
        notes = f"rampa 1-min máx {ramp_max:.0f} W → FTP {ftp_est:.0f}"
    elif minutes is not None and watts is not None:
        d = minutes * 60.0
        cp_val = watts - 20000.0 / d           # corrección W' nominal
        sd_cp = 8.0 if minutes >= 12 else 16.0  # esfuerzos cortos, menos fiables
        kind = "effort"
        notes = f"esfuerzo maximal {minutes:.0f} min @ {watts:.0f} W"
    else:
        typer.secho(
            "Indica un test: --ftp, o --cp [--wprime], o --minutes+--watts, o --ramp-max.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        store_test_result(
            session, athlete_id, when, kind, cp_val, sd_cp, wp_val, sd_wp, notes
        )

    typer.secho(
        f"Test registrado ({kind}, {when:%Y-%m-%d}): CP≈{cp_val:.0f} W. Re-estimando...\n",
        fg=typer.colors.CYAN,
    )
    with session_scope() as session:
        result = estimate_cp_service(session, athlete_id)
    if result is not None:
        _report_and_persist(athlete_id, result, store=True)


# --------------------------------------------------------------------------- #
@app.command("mark-test")
def mark_test(
    activity: int = typer.Option(None, help="Id de la actividad a marcar como test maximal."),
    date: str = typer.Option(None, help="Marcar la actividad de esa fecha YYYY-MM-DD."),
    last: bool = typer.Option(False, "--last", help="Marcar la última actividad con potencia."),
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
) -> None:
    """Marca una actividad de Strava como esfuerzo maximal (test) y re-estima.

    Usa la curva REAL de esa actividad como ancla de alta confianza — sin teclear
    ningún número. Elige la actividad por --activity, --date o --last.
    """
    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        if activity is not None:
            act = mark_activity_as_test(session, activity)
        elif date is not None:
            day = dateparser.parse(date).date()
            found = find_activity_on_date(session, athlete_id, day)
            act = mark_activity_as_test(session, found.id) if found else None
        elif last:
            found = latest_power_activity(session, athlete_id)
            act = mark_activity_as_test(session, found.id) if found else None
        else:
            typer.secho(
                "Indica --activity <id>, --date <YYYY-MM-DD> o --last.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)

        if act is None:
            typer.secho("No se encontró la actividad.", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        label = f"[{act.id}] {act.start_time:%Y-%m-%d}  {act.name or act.sport}"

    typer.secho(f"Marcada como test maximal: {label}. Re-estimando...\n", fg=typer.colors.CYAN)
    with session_scope() as session:
        result = estimate_cp_service(session, athlete_id)
    if result is not None:
        _report_and_persist(athlete_id, result, store=True)


# --------------------------------------------------------------------------- #
@app.command("backtest")
def backtest_cmd(
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
    window_days: int = typer.Option(42, help="Tamaño de ventana (no solapada)."),
) -> None:
    """Valida el modelo de CP: backtest one-step-ahead + calibración de la CI."""
    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        result = backtest_service(session, athlete_id, window_days=window_days)
    if result is None:
        typer.secho("Datos insuficientes para el backtest.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    typer.secho("Backtest one-step-ahead del CP:", fg=typer.colors.CYAN, bold=True)
    for line in result.summary().splitlines():
        typer.echo(f"  {line}")


# --------------------------------------------------------------------------- #
@app.command("tune-cp")
def tune_cp(
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
    save: bool = typer.Option(True, help="Guardar la config aprendida (model_config)."),
) -> None:
    """Aprende los hiperparámetros del filtro (máx. verosimilitud predictiva)."""
    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        result = tune_service(session, athlete_id, save=save)
    if result is None:
        typer.secho("Datos insuficientes para calibrar.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    learned, before, after = result
    typer.secho("ANTES (a ojo):", fg=typer.colors.YELLOW, bold=True)
    for line in before.summary().splitlines():
        typer.echo(f"  {line}")
    typer.secho("DESPUÉS (aprendido):", fg=typer.colors.GREEN, bold=True)
    for line in after.summary().splitlines():
        typer.echo(f"  {line}")
    typer.echo(
        f"\n  q_cp={learned.q_cp:.2f}  q_wp={learned.q_wp:.0f}  "
        f"obs_scale={learned.obs_noise_scale:.2f}  down_weight={learned.down_weight:.1f}"
    )
    if save:
        typer.secho("\nConfig persistida en model_config ✔", fg=typer.colors.GREEN)


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
