"""API JSON + servidor del frontend. Expone lo que el motor calcula; no decide
nada nuevo (reusa twin/planner/assistant). Pensada para reutilizarse desde una
app móvil el día de mañana."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cycling_coach.assistant.assistant import ChatSession
from cycling_coach.assistant.grounding import Facts, gather_facts, planning_date
from cycling_coach.assistant.llm import LLMClient, LLMError
from cycling_coach.auth import (
    MIN_PASSWORD_LEN,
    TOKEN_TTL_S,
    hash_password,
    make_token,
    parse_token,
    verify_password,
)
from cycling_coach.config import get_settings
from cycling_coach.db.engine import ensure_schema, session_scope
from cycling_coach.db.models import Activity
from cycling_coach.db.repositories import (
    add_goal,
    count_users,
    create_athlete,
    create_user,
    first_athlete_id,
    get_athlete,
    get_availability,
    get_or_create_secret,
    get_user,
    get_user_by_username,
    next_goal,
    save_profile,
    set_availability,
)
from cycling_coach.physiology import compute_ctl_atl_tsb
from cycling_coach.planner.planner import PlannedSession
from cycling_coach.planner.service import plan_horizon
from cycling_coach.sync import SyncError, sync_recent
from cycling_coach.twin.coherence_service import assess_cp_coherence, power_curve
from cycling_coach.twin.load_service import daily_load_and_intensity, smoothed_cp_states

_STATIC = Path(__file__).parent / "static"
_log = logging.getLogger("uvicorn.error")     # aparece en la salida del servidor


async def _sync_loop(interval_s: int) -> None:
    """Sincroniza con Strava en segundo plano cada `interval_s` mientras el
    servidor corre → los entrenamientos entran solos, sin abrir la app."""
    _log.info("Sync automático con Strava activo (cada %ds).", interval_s)
    while True:
        try:
            r = await asyncio.to_thread(sync_recent, fetch_streams=True)
            _log.info(
                "Sync automático: %d nuevas, %d ya existentes.",
                r.activities_ingested, r.skipped_existing,
            )
        except SyncError as exc:
            _log.warning("sync automático falló: %s", exc)
        except Exception:                       # nunca tumbar el bucle
            _log.exception("sync automático: error inesperado")
        await asyncio.sleep(interval_s)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    ensure_schema()                    # crea/actualiza el esquema (idempotente)
    interval = get_settings().sync_interval_s
    task = asyncio.create_task(_sync_loop(interval)) if interval > 0 else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()


def _db() -> Iterator[Session]:
    with session_scope() as session:
        yield session


DB = Annotated[Session, Depends(_db)]

_COOKIE = "vk_session"


def _first_athlete_id(session: Session) -> int:
    aid = first_athlete_id(session)
    if aid is None:
        raise HTTPException(404, "No hay atleta. Corre la ingesta primero.")
    return aid


def _current_athlete_id(request: Request, session: DB) -> int:
    """Atleta del usuario autenticado (por cookie). Si AUTH_ENABLED=false, usa el
    primer atleta (comportamiento previo: pestillo para no bloquearse nunca)."""
    if not get_settings().auth_enabled:
        return _first_athlete_id(session)
    uid = parse_token(request.cookies.get(_COOKIE), get_or_create_secret(session))
    user = get_user(session, uid) if uid is not None else None
    if user is None:
        raise HTTPException(401, "No autenticado")
    return user.athlete_id


AID = Annotated[int, Depends(_current_athlete_id)]


def _set_session_cookie(response: Response, user_id: int, session: Session) -> None:
    token = make_token(user_id, get_or_create_secret(session))
    response.set_cookie(
        _COOKIE, token, max_age=TOKEN_TTL_S, httponly=True, samesite="lax"
    )


def _plan_json(p: PlannedSession | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {
        "objective": p.objective.value,
        "session": p.template.name,
        "minutes": round(p.template.total_minutes()),
        "targets": p.targets,
        "rationale": p.rationale,
        "aspired": p.aspired.value if p.aspired else None,
    }


def _facts_json(f: Facts) -> dict[str, Any]:
    return {
        "ftp": f.ftp,
        "cp": f.cp,
        "w_prime": f.w_prime,
        "tsb": f.tsb,
        "ctl": f.ctl,
        "atl": f.atl,
        "cri": f.cri,
        "cri_coverage": f.cri_coverage,
        "goal_name": f.goal_name,
        "goal_date": f.goal_date.isoformat() if f.goal_date else None,
        "days_to_event": f.days_to_event,
        "phase": f.phase,
        "trained_today": f.trained_today,
        "trained_minutes": f.trained_minutes,
        "plan_date": f.plan_date.isoformat() if f.plan_date else None,
        "plan": _plan_json(f.plan),
        "thresholds": f.thresholds,
        "form_label": f.form_label,
    }


class AuthIn(BaseModel):
    username: str
    password: str


class ChatIn(BaseModel):
    message: str


class GoalIn(BaseModel):
    name: str | None = None
    date: str                       # ISO YYYY-MM-DD
    kind: str | None = None
    priority: str = "A"


class ProfileIn(BaseModel):
    name: str | None = None
    level: str | None = None            # principiante|intermedio|avanzado|elite
    declared_ftp_w: float | None = None
    sex: str | None = None              # M|F
    birthdate: str | None = None        # ISO YYYY-MM-DD
    height_cm: float | None = None
    weight_kg: float | None = None
    hr_max: int | None = None
    hr_rest: int | None = None
    weekly_minutes_target: int | None = None
    availability: dict[int, int] | None = None   # weekday 0=lunes → minutos
    goal_name: str | None = None
    goal_date: str | None = None                 # objetivo opcional del onboarding
    goal_kind: str | None = None                 # tipo de evento (gran_fondo, ruta…)
    goal_priority: str = "A"


def create_app() -> FastAPI:
    app = FastAPI(title="Vikon", docs_url="/api/docs", lifespan=_lifespan)
    chat_state: dict[str, ChatSession] = {}     # una conversación (single-user local)

    @app.middleware("http")
    async def _no_cache_static(request, call_next):  # dev: siempre servir estáticos frescos
        resp = await call_next(request)
        if request.url.path.startswith("/static") or request.url.path == "/":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/api/state")
    def state(session: DB, aid: AID) -> dict[str, Any]:
        facts = gather_facts(session, aid, date.today())
        return _facts_json(facts)

    @app.get("/api/horizon")
    def horizon(session: DB, aid: AID, days: int = 7) -> list[dict[str, Any]]:
        start, _ = planning_date(session, aid, date.today())   # si entrenó hoy, desde mañana
        return [
            {
                "day": h.day.isoformat(),
                "tsb": round(h.tsb, 1),
                "ctl": round(h.ctl, 1),
                "phase": h.phase.value,
                "objective": h.plan.objective.value,
                "session": h.plan.template.name,
                "minutes": round(h.plan.template.total_minutes()),
                "tss": round(h.tss),
                "targets": h.plan.targets,
            }
            for h in plan_horizon(session, aid, start, days=days)
        ]

    @app.get("/api/trend")
    def trend(session: DB, aid: AID, days: int = 90) -> list[dict[str, Any]]:
        """Serie de forma (CTL/ATL/TSB) de los últimos `days` días para gráficas."""
        dli = daily_load_and_intensity(session, aid, date.today())
        if not dli:
            return []
        series = compute_ctl_atl_tsb({d: v[0] for d, v in dli.items()})
        cutoff = date.today() - timedelta(days=days)
        return [
            {"day": p.day.isoformat(), "ctl": round(p.ctl, 1),
             "atl": round(p.atl, 1), "tsb": round(p.tsb, 1)}
            for p in series if p.day >= cutoff
        ]

    @app.get("/api/form-forecast")
    def form_forecast(
        session: DB, aid: AID, past: int = 60, future: int = 7
    ) -> list[dict[str, Any]]:
        """Forma REAL de los últimos `past` días + PROYECCIÓN del horizonte de los
        próximos `future` (para ver hacia dónde va tu forma). `projected` marca el
        tramo futuro (se dibuja punteado)."""
        dli = daily_load_and_intensity(session, aid, date.today())
        if not dli:
            return []
        series = compute_ctl_atl_tsb({d: v[0] for d, v in dli.items()})
        cutoff = date.today() - timedelta(days=past)
        past_pts = [p for p in series if p.day >= cutoff]
        out: list[dict[str, Any]] = [
            {"day": p.day.isoformat(), "ctl": round(p.ctl, 1),
             "tsb": round(p.tsb, 1), "projected": False}
            for p in past_pts
        ]
        last_real = past_pts[-1].day if past_pts else None
        start, _ = planning_date(session, aid, date.today())
        for h in plan_horizon(session, aid, start, days=future):
            if last_real is not None and h.day <= last_real:
                continue                       # evita duplicar el día de la unión
            out.append({"day": h.day.isoformat(), "ctl": round(h.ctl, 1),
                        "tsb": round(h.tsb, 1), "projected": True})
        return out

    @app.get("/api/ftp")
    def ftp_history(session: DB, aid: AID) -> list[dict[str, Any]]:
        """Evolución de FTP y CP en el tiempo (submuestreada, ~semanal)."""
        states = smoothed_cp_states(session, aid)
        out: list[dict[str, Any]] = []
        last: date | None = None
        for s in sorted(states, key=lambda s: s.as_of):
            d = s.as_of.date()
            if last is None or (d - last).days >= 7:
                out.append({"day": d.isoformat(), "ftp": round(s.ftp_w),
                            "cp": round(s.cp.mean)})
                last = d
        return out

    @app.get("/api/coherence")
    def coherence(session: DB, aid: AID) -> dict[str, Any]:
        r = assess_cp_coherence(session, aid, date.today())
        if r is None:
            raise HTTPException(404, "Sin CP/W' o potencia reciente.")
        return {
            "cp": r.cp, "w_prime": r.w_prime, "verdict": r.verdict,
            "coherent": r.coherent,
            "checks": [
                {"seconds": c.seconds, "actual": c.actual,
                 "predicted": round(c.predicted), "ratio": c.ratio, "exceeds": c.exceeds}
                for c in r.checks
            ],
        }

    @app.get("/api/power-curve")
    def power_curve_ep(session: DB, aid: AID, days: int = 120) -> dict[str, Any]:
        pc = power_curve(session, aid, date.today(), days=days)
        if pc is None:
            raise HTTPException(404, "Sin potencia reciente.")
        return pc

    @app.get("/api/settings")
    def settings_ep(session: DB, aid: AID) -> dict[str, Any]:
        cfg = get_settings()
        facts = gather_facts(session, aid, date.today())
        n_act = session.execute(
            select(func.count()).select_from(Activity).where(Activity.athlete_id == aid)
        ).scalar_one()
        last = session.execute(
            select(func.max(Activity.start_time)).where(Activity.athlete_id == aid)
        ).scalar_one_or_none()
        goal = next_goal(session, aid, date.today())
        return {
            "ftp": facts.ftp,
            "cp": facts.cp,
            "w_prime": facts.w_prime,
            "activities": n_act,
            "last_activity": last.date().isoformat() if last else None,
            "goal": (
                {
                    "name": goal.name,
                    "date": goal.event_date.isoformat(),
                    "priority": goal.priority,
                    "days_to": (goal.event_date - date.today()).days,
                }
                if goal
                else None
            ),
            "llm": {
                "configured": cfg.llm_configured,
                "model": cfg.llm_model,
                "base_url": cfg.llm_base_url,
            },
        }

    @app.get("/api/profile")
    def get_profile_ep(session: DB, aid: AID) -> dict[str, Any]:
        a = get_athlete(session, aid)
        goal = next_goal(session, aid, date.today())
        return {
            "onboarded": bool(a.onboarded) if a else False,
            "name": a.name if a else None,
            "level": a.level if a else None,
            "declared_ftp_w": a.declared_ftp_w if a else None,
            "sex": a.sex if a else None,
            "birthdate": a.birthdate.isoformat() if a and a.birthdate else None,
            "height_cm": a.height_cm if a else None,
            "weight_kg": a.weight_kg if a else None,
            "hr_max": a.hr_max if a else None,
            "hr_rest": a.hr_rest if a else None,
            "weekly_minutes_target": a.weekly_minutes_target if a else None,
            "availability": get_availability(session, aid),
            "goal": (
                {"name": goal.name, "date": goal.event_date.isoformat(),
                 "kind": goal.kind, "priority": goal.priority} if goal else None
            ),
        }

    @app.post("/api/profile")
    def save_profile_ep(body: ProfileIn, session: DB, aid: AID) -> dict[str, Any]:
        data: dict[str, Any] = {
            k: v for k, v in body.model_dump().items()
            if k not in ("availability", "birthdate", "goal_name", "goal_date")
            and v is not None
        }
        if body.birthdate:
            try:
                data["birthdate"] = date.fromisoformat(body.birthdate)
            except ValueError as exc:
                raise HTTPException(422, "Fecha de nacimiento inválida.") from exc
        save_profile(session, aid, data)
        if body.availability:
            set_availability(session, aid, body.availability)
        if body.goal_date:
            try:
                add_goal(
                    session, aid, date.fromisoformat(body.goal_date),
                    name=body.goal_name, kind=body.goal_kind, priority=body.goal_priority,
                )
            except ValueError as exc:
                raise HTTPException(422, "Fecha de objetivo inválida.") from exc
        return {"ok": True}

    @app.post("/api/goal")
    def set_goal_ep(body: GoalIn, session: DB, aid: AID) -> dict[str, Any]:
        try:
            event = date.fromisoformat(body.date)
        except ValueError as exc:
            raise HTTPException(422, "Fecha inválida (usa AAAA-MM-DD).") from exc
        add_goal(session, aid, event, name=body.name, kind=body.kind, priority=body.priority)
        return {"ok": True, "days_to": (event - date.today()).days}

    @app.post("/api/sync")
    def sync_full(aid: AID) -> dict[str, Any]:
        """Sincronización manual COMPLETA (con streams) — botón de Ajustes."""
        try:
            r = sync_recent(fetch_streams=True)
        except SyncError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {
            "new": r.activities_ingested,
            "streams": r.streams_ingested,
            "skipped": r.skipped_existing,
        }

    @app.post("/api/refresh")
    def refresh(aid: AID) -> dict[str, Any]:
        """Sincroniza las actividades nuevas de Strava (incremental). La llama la
        app al abrir → el plan refleja la salida de hoy sin backfill manual."""
        try:
            r = sync_recent(fetch_streams=False)   # rápido al abrir; streams en backfill/sync
        except SyncError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {
            "new": r.activities_ingested,
            "streams": r.streams_ingested,
            "skipped": r.skipped_existing,
        }

    @app.post("/api/chat")
    def chat(body: ChatIn, session: DB, aid: AID) -> dict[str, Any]:
        key = str(aid)
        try:
            if key not in chat_state:
                chat_state[key] = ChatSession(aid, date.today(), LLMClient.from_settings())
            reply = chat_state[key].turn(session, body.message)
        except LLMError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {
            "text": reply.text,
            "intent": {
                "kind": reply.intent.kind,
                "minutes": reply.intent.minutes,
                "readiness": reply.intent.readiness,
            },
            "logged": reply.logged,
            "plan": _plan_json(reply.facts.plan),
        }

    # --- Autenticación (cuentas) ---------------------------------------------
    @app.get("/api/me")
    def me(request: Request, session: DB) -> dict[str, Any]:
        """Estado de sesión: si la auth está activa y si hay sesión válida."""
        if not get_settings().auth_enabled:
            return {"auth_required": False, "authenticated": True, "username": None,
                    "has_users": True}
        uid = parse_token(request.cookies.get(_COOKIE), get_or_create_secret(session))
        user = get_user(session, uid) if uid is not None else None
        return {
            "auth_required": True,
            "authenticated": user is not None,
            "username": user.username if user else None,
            "has_users": count_users(session) > 0,
        }

    @app.post("/api/register")
    def register(body: AuthIn, response: Response, session: DB) -> dict[str, Any]:
        username = body.username.strip()
        if len(username) < 3:
            raise HTTPException(422, "El usuario debe tener al menos 3 caracteres.")
        if len(body.password) < MIN_PASSWORD_LEN:
            raise HTTPException(422, f"La contraseña debe tener ≥{MIN_PASSWORD_LEN} caracteres.")
        if get_user_by_username(session, username) is not None:
            raise HTTPException(409, "Ese usuario ya existe.")
        # El PRIMER registro reclama el atleta existente (con todos tus datos);
        # los siguientes crean un atleta nuevo (vacío hasta conectar Strava).
        if count_users(session) == 0:
            aid = first_athlete_id(session) or create_athlete(session, name=username)
        else:
            aid = create_athlete(session, name=username)
        pw_hash, pw_salt = hash_password(body.password)
        user = create_user(session, username, pw_hash, pw_salt, aid)
        _set_session_cookie(response, user.id, session)
        return {"ok": True, "username": username}

    @app.post("/api/login")
    def login(body: AuthIn, response: Response, session: DB) -> dict[str, Any]:
        user = get_user_by_username(session, body.username.strip())
        if user is None or not verify_password(body.password, user.pw_hash, user.pw_salt):
            raise HTTPException(401, "Usuario o contraseña incorrectos.")
        _set_session_cookie(response, user.id, session)
        return {"ok": True, "username": user.username}

    @app.post("/api/logout")
    def logout(response: Response) -> dict[str, Any]:
        response.delete_cookie(_COOKIE)
        return {"ok": True}

    if _STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=_STATIC), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(_STATIC / "index.html")

    return app


app = create_app()
