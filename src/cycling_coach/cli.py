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
from pathlib import Path

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
from cycling_coach.assistant.assistant import ChatSession, explain_today
from cycling_coach.assistant.assistant import ask as assistant_ask
from cycling_coach.assistant.llm import LLMClient, LLMError
from cycling_coach.config import get_settings
from cycling_coach.db.engine import get_engine, session_scope
from cycling_coach.db.models import Activity, Athlete, Base, DailyMetric, Stream
from cycling_coach.db.repositories import (
    add_goal,
    find_activity_on_date,
    latest_power_activity,
    list_goals,
    mark_activity_as_test,
    store_parameter_estimate,
    store_test_result,
    upsert_daily_metric,
)
from cycling_coach.domain.models import CanonicalDailyMetric, Estimate
from cycling_coach.ingest import backfill as run_backfill
from cycling_coach.oauth_loopback import wait_for_code
from cycling_coach.planner.service import plan_horizon, plan_today
from cycling_coach.twin import build_state
from cycling_coach.twin import estimate_cp as estimate_cp_service
from cycling_coach.twin.coherence_service import assess_cp_coherence
from cycling_coach.twin.cp_estimation import CPEstimationResult
from cycling_coach.twin.cp_estimation import backtest as backtest_service
from cycling_coach.twin.cp_estimation import tune as tune_service
from cycling_coach.twin.cri_service import calibrate_cri as calibrate_cri_service
from cycling_coach.twin.cri_service import compute_cri_service
from cycling_coach.twin.load_service import compute_and_store_load

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
        conn.execute(
            text("ALTER TABLE model_config ADD COLUMN IF NOT EXISTS cri_weights jsonb")
        )
    typer.secho("Esquema creado ✔", fg=typer.colors.GREEN)


# --------------------------------------------------------------------------- #
@app.command("strava-auth")
def strava_auth(
    athlete_id: int = typer.Option(
        None, help="Atleta al que enlazar la cuenta (por defecto, el primero)."
    ),
) -> None:
    """Lanza el flujo OAuth de Strava y persiste los tokens.

    Con varios perfiles, indica `--athlete-id`: sin él la cuenta se enlaza al
    PRIMER atleta, que casi nunca es lo que quieres al añadir a otra persona.
    Desde la app cada usuario lo hace solo, en Ajustes → Conectar mi Strava."""
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
        target = athlete_id if athlete_id is not None else accounts.ensure_athlete(session).id
        owner = accounts.account_owner(session, "strava", tokens.athlete_id or "")
        if owner is not None and owner != target:
            typer.secho(
                f"Esa cuenta de Strava ya está enlazada al atleta {owner}. "
                "Una cuenta pertenece a un solo perfil.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)
        accounts.save_tokens(session, target, "strava", tokens)

    typer.secho(
        f"Autorizado ✔  atleta local={target}, Strava id={tokens.athlete_id}, "
        f"scope={tokens.scope}",
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
        shown = f"{v:.1f}" if isinstance(v, float) else v
        typer.echo(f"    {k:14} = {shown}")
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
@app.command("compute-load")
def compute_load(
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
) -> None:
    """Calcula CTL/ATL/TSB (fitness/fatiga/forma) de tus entrenamientos y los
    guarda en el gemelo. Usa el FTP estimado (ejecuta antes `cc estimate-cp`)."""
    from datetime import datetime

    today = datetime.now(UTC).date()
    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        result = compute_and_store_load(session, athlete_id, today)
    if result is None:
        typer.secho(
            "Falta FTP o actividades. Ejecuta `cc estimate-cp` y `cc backfill`.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)
    c = result.current
    typer.secho(
        f"Carga @ {c.day:%Y-%m-%d} (FTP={result.ftp:.0f}W)",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(f"  CTL (fitness) = {c.ctl:.0f}")
    typer.echo(f"  ATL (fatiga)  = {c.atl:.0f}")
    typer.echo(f"  TSB (forma)   = {c.tsb:+.0f}")
    typer.echo(f"  ({result.n_activities} actividades, {result.n_days} días)")
    typer.secho("Guardado en el gemelo (daily) ✔", fg=typer.colors.GREEN)


# --------------------------------------------------------------------------- #
@app.command("checkin")
def checkin(
    sleep: float = typer.Option(None, help="Horas de sueño anoche."),
    feel: float = typer.Option(None, help="Sensación / disposición hoy (1–10)."),
    date: str = typer.Option(None, help="Fecha YYYY-MM-DD (por defecto, hoy)."),
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
) -> None:
    """Check-in diario manual (sueño y sensación) → alimenta la recuperación del
    CRI sin ningún wearable. Es el 'pop-up' diario, por detrás."""
    if sleep is None and feel is None:
        typer.secho("Indica al menos --sleep o --feel.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    day = dateparser.parse(date).date() if date else datetime.now(UTC).date()
    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        if sleep is not None:
            upsert_daily_metric(
                session, athlete_id,
                CanonicalDailyMetric("sleep_hours", day, sleep, "manual"),
            )
        if feel is not None:
            upsert_daily_metric(
                session, athlete_id,
                CanonicalDailyMetric("readiness", day, feel, "manual"),
            )
    typer.secho(f"Check-in guardado ({day:%Y-%m-%d}) ✔", fg=typer.colors.GREEN)


# --------------------------------------------------------------------------- #
@app.command("cri")
def cri_cmd(
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
) -> None:
    """CRI — índice de forma (v1: rendimiento + frescura + tendencia)."""
    from datetime import datetime

    today = datetime.now(UTC).date()
    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        detail = compute_cri_service(session, athlete_id, today)
    if detail is None:
        typer.secho("Datos insuficientes (ejecuta backfill/estimate-cp).", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    r = detail.result
    typer.secho(f"CRI = {r.cri:.0f}/100", fg=typer.colors.CYAN, bold=True)
    for k, v in r.components.items():
        typer.echo(f"  {k:12} = {v:.2f}")
    # Los componentes ausentes son OPCIONALES (mejoran el índice, no lo invalidan).
    hints = {
        "recovery": "haz `cc checkin --sleep --feel`",
        "compliance": "requiere plan (Fase 3)",
    }
    for k in r.missing:
        typer.echo(f"  {k:12} = —   (opcional: {hints.get(k, '')})")


# --------------------------------------------------------------------------- #
@app.command("tune-cri")
def tune_cri(
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
    save: bool = typer.Option(True, help="Guardar los pesos calibrados."),
) -> None:
    """Calibra los pesos del CRI contra tu rendimiento real (cap. 5.3)."""
    from datetime import datetime

    today = datetime.now(UTC).date()
    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        cal = calibrate_cri_service(session, athlete_id, today, save=save)
    if cal is None:
        typer.secho("Datos insuficientes para calibrar el CRI.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    typer.secho(f"Calibración CRI (n={cal.n} días con esfuerzo):", fg=typer.colors.CYAN, bold=True)
    for k, v in cal.weights.items():
        typer.echo(f"  {k:12} = {v:.2f}")
    typer.echo(
        f"  correlación con rendimiento: defaults={cal.corr_default:+.2f} → "
        f"aprendidos={cal.corr_learned:+.2f}"
    )
    verdict = "MEJORA" if cal.improved else "no mejora (se mantienen ~defaults)"
    typer.secho(f"  => {verdict}", fg=typer.colors.GREEN if cal.improved else typer.colors.YELLOW)


# --------------------------------------------------------------------------- #
@app.command("plan")
def plan_cmd(
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
    minutes: float = typer.Option(None, help="Tiempo disponible hoy (min): ajusta la dosis."),
) -> None:
    """Sesión recomendada de hoy (objetivo → entrenamiento → explicación)."""
    from datetime import datetime

    today = datetime.now(UTC).date()
    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        plan = plan_today(session, athlete_id, today, minutes=minutes)
    if plan is None:
        typer.secho("Falta el FTP. Ejecuta `cc estimate-cp` primero.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    typer.secho(f"Plan de hoy — {plan.template.name}", fg=typer.colors.CYAN, bold=True)
    if plan.aspired is not None:
        typer.secho(
            f"  (rebajado desde {plan.aspired.value}; ver motivo)",
            fg=typer.colors.YELLOW,
        )
    typer.echo(f"  {plan.rationale}")
    typer.echo(f"  Duración ≈ {plan.template.total_minutes():.0f} min  (FTP {plan.ftp:.0f} W)")
    if plan.targets:
        typer.echo("  Bloques:")
        for line in plan.targets:
            typer.echo(f"    • {line}")
    else:
        typer.echo("  (sin bloques — descanso)")


# --------------------------------------------------------------------------- #
@app.command("explain")
def explain_cmd(
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
) -> None:
    """Narra el plan de hoy en lenguaje natural (el LLM redacta, no decide)."""
    from datetime import datetime

    today = datetime.now(UTC).date()
    try:
        with session_scope() as session:
            athlete_id = _resolve_athlete_id(session, athlete_id)
            reply = explain_today(session, athlete_id, today)
    except LLMError as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from exc
    typer.secho("Vikon:", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  {reply.text}")


@app.command("ask")
def ask_cmd(
    message: str = typer.Argument(..., help="Lo que quieras decirle a Vikon."),
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
    show_plan: bool = typer.Option(False, "--plan", help="Muestra también el plan determinista."),
) -> None:
    """Habla con Vikon: traduce tu mensaje → el motor decide → te lo explica.

    Ej.: cc ask "solo tengo 40 min y me siento reventado"
    """
    from datetime import datetime

    today = datetime.now(UTC).date()
    try:
        with session_scope() as session:
            athlete_id = _resolve_athlete_id(session, athlete_id)
            reply = assistant_ask(session, athlete_id, today, message)
            plan = reply.facts.plan
            plan_line = None
            if show_plan and plan is not None:
                plan_line = (
                    f"{plan.objective.value} — {plan.template.name} "
                    f"({plan.template.total_minutes():.0f} min)"
                )
    except LLMError as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from exc

    if reply.logged:
        typer.secho(
            f"✓ registrado: {', '.join(reply.logged)}", fg=typer.colors.GREEN
        )
    it = reply.intent
    interp = []
    if it.minutes is not None:
        interp.append(f"{it.minutes:.0f} min")
    if it.readiness:
        interp.append(f"disposición {it.readiness}")
    if interp:
        typer.secho(f"(interpreté: {', '.join(interp)})", fg=typer.colors.BRIGHT_BLACK)
    typer.secho("Vikon:", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  {reply.text}")
    if plan_line:
        typer.secho(f"  → plan: {plan_line}", fg=typer.colors.BRIGHT_BLACK)


# --------------------------------------------------------------------------- #
@app.command("sync")
def sync_cmd(
    no_streams: bool = typer.Option(
        False, "--no-streams", help="No descargar streams (más rápido)."
    ),
    all_profiles: bool = typer.Option(
        False, "--all", help="Sincroniza TODOS los perfiles conectados."
    ),
    athlete_id: int = typer.Option(None, help="Sincroniza solo ese atleta."),
) -> None:
    """Sincroniza SOLO lo nuevo desde tu última actividad (incremental, rápido).

    Ideal para una tarea programada (cron / Programador de tareas de Windows).
    Es el `backfill` en pequeño: la app lo llama sola al abrir. Con varios
    perfiles usa `--all` para que entren los entrenamientos de todos."""
    from cycling_coach.sync import SyncError, sync_all, sync_recent

    if all_profiles:
        results = sync_all(fetch_streams=not no_streams)
        if not results:
            typer.secho("Ningún perfil tiene Strava conectado.", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1)
        for aid, r in results.items():
            if isinstance(r, SyncError):
                typer.secho(f"atleta {aid}: {r}", fg=typer.colors.YELLOW)
            else:
                typer.secho(
                    f"atleta {aid} ✔  nuevas={r.activities_ingested}  "
                    f"streams={r.streams_ingested}  ya_existentes={r.skipped_existing}",
                    fg=typer.colors.GREEN,
                )
        return

    try:
        r = sync_recent(athlete_id=athlete_id, fetch_streams=not no_streams)
    except SyncError as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from exc
    typer.secho(
        f"Sincronizado ✔  nuevas={r.activities_ingested}  "
        f"streams={r.streams_ingested}  ya_existentes={r.skipped_existing}",
        fg=typer.colors.GREEN,
    )


# --------------------------------------------------------------------------- #
def _lan_ip() -> str | None:
    """IP de este PC en la red local (para abrir la app desde el móvil). No
    envía tráfico: solo consulta qué interfaz saldría hacia fuera."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no viaja ningún paquete
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


@app.command("serve")
def serve_cmd(
    port: int = typer.Option(8730, help="Puerto del servidor web."),
    host: str = typer.Option(
        "0.0.0.0", help="Host. 0.0.0.0 = accesible desde el móvil en la misma WiFi."
    ),
    reload: bool = typer.Option(
        True, help="Recarga sola al cambiar el código (desarrollo). --no-reload para apagarlo."
    ),
) -> None:
    """Lanza la UI web (FastAPI) — pantalla Hoy + horizonte + chat con Vikon.

    Con `--reload` (por defecto) el servidor se reinicia solo cuando cambia el
    código: no hace falta pararlo y arrancarlo a mano tras cada actualización."""
    import uvicorn

    typer.secho(f"Vikon en http://localhost:{port}", fg=typer.colors.CYAN, bold=True)
    if host == "0.0.0.0":
        lan = _lan_ip()
        if lan:
            typer.secho(
                f"  Desde el móvil (misma WiFi):  http://{lan}:{port}",
                fg=typer.colors.GREEN, bold=True,
            )
        typer.secho(
            "  (si el móvil no conecta, permite el puerto en el firewall de Windows)",
            fg=typer.colors.BRIGHT_BLACK,
        )
    if reload:
        typer.secho(
            "  Recarga automática ACTIVA: al actualizar el código se reinicia solo.",
            fg=typer.colors.BRIGHT_BLACK,
        )
    uvicorn.run(
        "cycling_coach.web.api:app", host=host, port=port, reload=reload,
        reload_dirs=[str(Path(__file__).resolve().parent)] if reload else None,
    )


# --------------------------------------------------------------------------- #
@app.command("coherence")
def coherence_cmd(
    days: int = typer.Option(120, help="Ventana reciente (días) de esfuerzos a contrastar."),
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
) -> None:
    """Coherencia/maximalidad del CP: contrasta el CP/W' vigente con tus
    esfuerzos reales recientes (avisa si está obsoleto o sin confirmar)."""
    from datetime import datetime

    today = datetime.now(UTC).date()
    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        report = assess_cp_coherence(session, athlete_id, today, days=days)
    if report is None:
        typer.secho("Falta CP/W' o no hay potencia reciente. Corre `cc estimate-cp`.",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    typer.secho(
        f"Coherencia del CP ({report.cp:.0f} W, W' {report.w_prime / 1000:.1f} kJ) "
        f"— últimos {days} días",
        fg=typer.colors.CYAN, bold=True,
    )
    typer.echo(f"  {'dur':>6} {'real':>7} {'modelo':>7} {'ratio':>6}")
    for c in report.checks:
        mark = "  <-- supera" if c.exceeds else ""
        dur = f"{c.seconds // 60}min" if c.seconds >= 60 else f"{c.seconds}s"
        ratio = f"{c.ratio * 100:.0f}%" if c.ratio else "—"
        actual = f"{c.actual:.0f}" if c.actual else "—"
        typer.echo(f"  {dur:>6} {actual:>7} {c.predicted:>7.0f} {ratio:>6}{mark}")
    color = typer.colors.RED if report.violations else typer.colors.GREEN
    typer.secho(f"\n  {report.verdict}", fg=color)


# --------------------------------------------------------------------------- #
@app.command("chat")
def chat_cmd(
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
) -> None:
    """Conversa con Vikon (multivuelta). 'salir' para terminar.

    Recuerda el contexto: di "solo tengo 40 min" y luego "¿por qué?" y sigue
    hablando del mismo plan. El motor decide; Vikon explica.
    """
    from datetime import datetime

    try:
        llm = LLMClient.from_settings()
    except LLMError as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from exc

    today = datetime.now(UTC).date()
    typer.secho("Vikon — chat. Escribe 'salir' para terminar.\n", fg=typer.colors.CYAN, bold=True)
    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        chat = ChatSession(athlete_id, today, llm)
        while True:
            try:
                msg = typer.prompt("Tú")
            except (EOFError, KeyboardInterrupt):
                break
            if msg.strip().lower() in ("salir", "exit", "quit"):
                break
            if not msg.strip():
                continue
            try:
                reply = chat.turn(session, msg)
            except LLMError as exc:
                typer.secho(f"  (error del LLM: {exc})", fg=typer.colors.YELLOW)
                continue
            if reply.logged:
                typer.secho(f"  ✓ registrado: {', '.join(reply.logged)}", fg=typer.colors.GREEN)
            hint = []
            if reply.intent.minutes is not None:
                hint.append(f"{reply.intent.minutes:.0f} min")
            if reply.intent.readiness:
                hint.append(reply.intent.readiness)
            tag = f"  ({', '.join(hint)})" if hint else ""
            typer.secho(f"Vikon:{tag}", fg=typer.colors.CYAN, bold=True)
            typer.echo(f"  {reply.text}\n")
    typer.secho("¡Hasta luego!", fg=typer.colors.CYAN)


# --------------------------------------------------------------------------- #
@app.command("horizon")
def horizon_cmd(
    days: int = typer.Option(7, help="Días a proyectar."),
    minutes: float = typer.Option(None, help="Tiempo disponible por día (min)."),
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
) -> None:
    """Microciclo proyectado (rollout simulado). Solo HOY se compromete; el
    resto se re-planifica al llegar datos reales (horizonte deslizante)."""
    from datetime import datetime

    today = datetime.now(UTC).date()
    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        horizon = plan_horizon(session, athlete_id, today, days=days, minutes=minutes)
    if not horizon:
        typer.secho("Falta FTP o carga. Ejecuta `cc estimate-cp` y `cc compute-load`.",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    typer.secho(f"Horizonte {days} días (solo hoy se compromete):",
                fg=typer.colors.CYAN, bold=True)
    for i, h in enumerate(horizon):
        tag = "HOY" if i == 0 else h.day.strftime("%a")
        ph = f" · {h.phase.value}" if h.phase.value != "off" else ""
        typer.echo(
            f"  {tag:>4} {h.day.isoformat()}  TSB {h.tsb:+5.1f}{ph}  "
            f"→ {h.plan.objective.value:<10} {h.plan.template.name:<18} "
            f"(TSS~{h.tss:.0f}, {h.plan.template.total_minutes():.0f}')"
        )
    total = sum(h.tss for h in horizon)
    end_ctl = horizon[-1].ctl
    typer.echo(
        f"  Σ TSS {total:.0f} · CTL {horizon[0].ctl:.0f}→{end_ctl:.0f} "
        f"· TSB final proyectado {horizon[-1].tsb:+.0f}"
    )
    typer.secho(f"\n  Hoy en detalle:\n    {horizon[0].plan.rationale}", dim=True)


# --------------------------------------------------------------------------- #
@app.command("set-goal")
def set_goal_cmd(
    event_date: str = typer.Argument(..., help="Fecha del evento (YYYY-MM-DD)."),
    name: str = typer.Option(None, help="Nombre del evento."),
    kind: str = typer.Option(None, help="Tipo: road_race|gran_fondo|tt|..."),
    priority: str = typer.Option("A", help="Prioridad: A|B|C."),
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
) -> None:
    """Registra un evento objetivo: da al planner un horizonte de temporada."""
    from datetime import date as _date

    day = _date.fromisoformat(event_date)
    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        goal = add_goal(session, athlete_id, day, name=name, kind=kind, priority=priority)
        days = (goal.event_date - _date.today()).days
    typer.secho(
        f"Meta guardada: {name or kind or 'evento'} el {event_date} "
        f"(faltan {days} días).",
        fg=typer.colors.GREEN,
    )


@app.command("goals")
def goals_cmd(
    athlete_id: int = typer.Option(None, help="Id del atleta (por defecto, el primero)."),
) -> None:
    """Lista los eventos objetivo del atleta."""
    from datetime import date as _date

    today = _date.today()
    with session_scope() as session:
        athlete_id = _resolve_athlete_id(session, athlete_id)
        goals = list_goals(session, athlete_id)
        rows = [
            (g.event_date.isoformat(), (g.event_date - today).days,
             g.priority, g.name or g.kind or "—")
            for g in goals
        ]
    if not rows:
        typer.echo("Sin metas. Usa `cc set-goal YYYY-MM-DD`.")
        return
    for iso, days, prio, label in rows:
        tag = f"faltan {days} d" if days >= 0 else f"hace {-days} d"
        typer.echo(f"  [{prio}] {iso}  ({tag})  {label}")


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
