"""API JSON + servidor del frontend. Expone lo que el motor calcula; no decide
nada nuevo (reusa twin/planner/assistant). Pensada para reutilizarse desde una
app móvil el día de mañana."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
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
    clear_plan_override,
    count_users,
    create_athlete,
    create_user,
    first_athlete_id,
    get_athlete,
    get_availability,
    get_availability_overrides,
    get_or_create_secret,
    get_plan_log,
    get_plan_overrides,
    get_user,
    get_user_by_username,
    latest_daily_metric,
    log_plan,
    next_goal,
    save_profile,
    set_availability,
    set_availability_override,
    set_plan_override,
    upsert_daily_metric,
)
from cycling_coach.domain.models import CanonicalDailyMetric
from cycling_coach.physiology import compute_ctl_atl_tsb
from cycling_coach.planner.library import Objective
from cycling_coach.planner.planner import PlannedSession
from cycling_coach.planner.service import plan_horizon
from cycling_coach.planner.simulator import estimate_session_tss, session_intensity
from cycling_coach.sync import SyncError, sync_recent
from cycling_coach.twin.activity_service import activity_detail, list_activities
from cycling_coach.twin.autocalibrate import autocalibrate
from cycling_coach.twin.coherence_service import assess_cp_coherence, power_curve
from cycling_coach.twin.compliance import compliance_report
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
            # El FTP/CP se recalibra solo cuando llegan datos que lo justifican
            # (los vatios del plan salen de ahí; antes solo se actualizaba a mano).
            if r.activities_ingested:
                out = await asyncio.to_thread(_recalibrate)
                if out and out.ran:
                    delta = f" ({out.delta_ftp:+.0f} W)" if out.delta_ftp else ""
                    _log.info(
                        "Autocalibración: FTP %.0f W, CP %.0f W%s — %s",
                        out.ftp, out.cp, delta, out.reason,
                    )
        except SyncError as exc:
            _log.warning("sync automático falló: %s", exc)
        except Exception:                       # nunca tumbar el bucle
            _log.exception("sync automático: error inesperado")
        await asyncio.sleep(interval_s)


def _warm_cache() -> None:
    """Precalcula el suavizador de CP al arrancar.

    Medido: 7.8 s en frío vs 0.007 s cacheado, y es el 97% del tiempo de
    /api/state. Hacerlo aquí mueve esa espera a un momento en que el usuario
    no está mirando, en vez de cobrársela en su primera pantalla."""
    try:
        with session_scope() as session:
            aid = first_athlete_id(session)
            if aid is None:
                return
            t0 = time.perf_counter()
            smoothed_cp_states(session, aid)
            _log.info("Caché de CP lista en %.1f s.", time.perf_counter() - t0)
    except Exception:
        _log.exception("precalentado de caché: error (no bloquea)")


def _recalibrate():
    """Reestima CP/W'/FTP del atleta si hay datos nuevos. Nunca revienta el
    bucle de sync: un fallo aquí no debe cortar la ingesta."""
    try:
        with session_scope() as session:
            aid = first_athlete_id(session)
            if aid is None:
                return None
            return autocalibrate(session, aid)
    except Exception:
        _log.exception("autocalibración: error inesperado")
        return None


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    ensure_schema()                    # crea/actualiza el esquema (idempotente)
    # Calienta la caché pesada YA, en segundo plano: la primera pantalla del
    # usuario no debe pagar los ~8 s del suavizador de CP.
    warm = asyncio.create_task(asyncio.to_thread(_warm_cache))
    interval = get_settings().sync_interval_s
    task = asyncio.create_task(_sync_loop(interval)) if interval > 0 else None
    try:
        yield
    finally:
        warm.cancel()
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


class CheckinIn(BaseModel):
    sleep_hours: float | None = None    # horas dormidas anoche
    feel: float | None = None           # sensación / disposición 1–10
    date: str | None = None             # ISO YYYY-MM-DD (por defecto, hoy)


class DayPlanIn(BaseModel):
    date: str                        # ISO YYYY-MM-DD
    minutes: int | None = None       # disponibilidad de ESE día (0 = libre)
    objective: str | None = None     # entrenamiento elegido; "auto" = que decida el motor


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
        # Deja constancia de lo prescrito: sin esto no se puede medir después si
        # el plan se siguió (el horizonte se recalcula y se perdería).
        if facts.plan is not None and facts.plan_date is not None:
            log_plan(
                session, aid, facts.plan_date, facts.plan.objective.value,
                facts.plan.template.name,
                estimate_session_tss(facts.plan.template),
            )
        return _facts_json(facts)

    @app.get("/api/compliance")
    def compliance_ep(session: DB, aid: AID, days: int = 28) -> dict[str, Any]:
        """Cumplimiento: lo prescrito vs lo entrenado en los últimos `days`."""
        end = date.today() - timedelta(days=1)      # hoy aún puede completarse
        start = end - timedelta(days=days - 1)
        plan = get_plan_log(session, aid, start, end)
        if not plan:
            return {
                "days": [], "rate": None, "n_planned": 0, "n_followed": 0,
                "note": "Aún no hay plan registrado. Se va guardando cada día "
                        "que abres la app.",
            }
        rep = compliance_report(session, aid, plan)
        return {
            "rate": round(rep.rate, 2),
            "n_planned": rep.n_planned,
            "n_followed": rep.n_followed,
            "tss_planned": rep.tss_planned,
            "tss_done": rep.tss_done,
            "load_ratio": round(rep.load_ratio, 2) if rep.load_ratio else None,
            "days": [
                {
                    "day": d.day.isoformat(), "planned": d.planned_objective,
                    "done": d.done_kind, "status": d.status, "note": d.note,
                    "planned_tss": round(d.planned_tss) if d.planned_tss else None,
                    "done_tss": d.done_tss, "minutes": d.done_minutes,
                }
                for d in rep.days
            ],
        }

    @app.get("/api/checkin")
    def checkin_get(session: DB, aid: AID) -> dict[str, Any]:
        """Check-in de hoy: sueño y sensación. Alimenta la Recuperación del CRI
        sin ningún wearable (autoinforme = señal validada de disposición)."""
        today = date.today()
        sleep = latest_daily_metric(session, aid, "sleep_hours", today)
        feel = latest_daily_metric(session, aid, "readiness", today)
        sleep_today = sleep[1] if sleep and sleep[0] == today else None
        feel_today = feel[1] if feel and feel[0] == today else None
        return {
            "day": today.isoformat(),
            "sleep_hours": sleep_today,
            "feel": feel_today,
            # `pending` dispara el saludo matinal del chat: si aún no has
            # contado cómo has dormido, te lo pregunta al abrir.
            "pending": sleep_today is None and feel_today is None,
            "last_sleep": sleep[1] if sleep else None,      # para prerrellenar
            "last_feel": feel[1] if feel else None,
        }

    @app.post("/api/checkin")
    def checkin_post(session: DB, aid: AID, body: CheckinIn) -> dict[str, Any]:
        day = date.fromisoformat(body.date) if body.date else date.today()
        if body.sleep_hours is None and body.feel is None:
            raise HTTPException(400, "Indica horas de sueño o sensación.")
        saved: list[str] = []
        if body.sleep_hours is not None:
            if not 0 <= body.sleep_hours <= 16:
                raise HTTPException(400, "Horas de sueño fuera de rango (0–16).")
            upsert_daily_metric(
                session, aid,
                CanonicalDailyMetric("sleep_hours", day, body.sleep_hours, "manual"),
            )
            saved.append("sueño")
        if body.feel is not None:
            if not 1 <= body.feel <= 10:
                raise HTTPException(400, "La sensación va de 1 a 10.")
            upsert_daily_metric(
                session, aid,
                CanonicalDailyMetric("readiness", day, body.feel, "manual"),
            )
            saved.append("sensación")
        return {"ok": True, "day": day.isoformat(), "saved": saved}

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
                "rationale": h.plan.rationale,
                "aspired": h.plan.aspired.value if h.plan.aspired else None,
                "atl": round(h.atl, 1),
                "intensity": round(session_intensity(h.plan.template), 2),
                "description": h.plan.template.description,
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

    @app.get("/api/activities")
    def activities(session: DB, aid: AID, limit: int = 30) -> list[dict[str, Any]]:
        """Entrenamientos recientes con métricas + resumen en texto (determinista)."""
        return [
            {
                "id": a.id, "day": a.day.isoformat(), "name": a.name, "sport": a.sport,
                "minutes": a.minutes, "distance_km": a.distance_km,
                "elevation_m": a.elevation_m, "avg_power_w": a.avg_power_w,
                "np_w": a.np_w, "max_power_w": a.max_power_w, "avg_hr": a.avg_hr,
                "kilojoules": a.kilojoules, "intensity": a.intensity, "tss": a.tss,
                "text": a.text, "session_label": a.session_label,
                "session_kind": a.session_kind, "detected": a.detected,
            }
            for a in list_activities(session, aid, limit=limit)
        ]

    @app.get("/api/activity/{activity_id}")
    def activity_one(session: DB, aid: AID, activity_id: int) -> dict[str, Any]:
        """Ficha completa de un entrenamiento: métricas, zonas e intervalos."""
        d = activity_detail(session, aid, activity_id)
        if d is None:
            raise HTTPException(404, "Entrenamiento no encontrado.")
        return d

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

    @app.post("/api/day")
    def set_day(body: DayPlanIn, session: DB, aid: AID) -> dict[str, Any]:
        """Ajusta un DÍA concreto: su disponibilidad y/o el entrenamiento que
        quieres hacer. 'auto' devuelve la decisión al motor."""
        try:
            day = date.fromisoformat(body.date)
        except ValueError as exc:
            raise HTTPException(422, "Fecha inválida (AAAA-MM-DD).") from exc
        if body.minutes is not None:
            if not 0 <= body.minutes <= 600:
                raise HTTPException(422, "Minutos fuera de rango (0–600).")
            set_availability_override(session, aid, day, body.minutes)
        if body.objective is not None:
            if body.objective in ("auto", ""):
                clear_plan_override(session, aid, day)
            elif body.objective in {o.value for o in Objective}:
                set_plan_override(session, aid, day, body.objective)
            else:
                raise HTTPException(422, "Objetivo desconocido.")
        return {"ok": True}

    @app.get("/api/day/{day}")
    def get_day(day: str, session: DB, aid: AID) -> dict[str, Any]:
        """Ajustes vigentes de un día: disponibilidad puntual y elección."""
        try:
            d = date.fromisoformat(day)
        except ValueError as exc:
            raise HTTPException(422, "Fecha inválida.") from exc
        avail = get_availability(session, aid)
        over = get_availability_overrides(session, aid, d, d)
        chosen = get_plan_overrides(session, aid, d, d)
        return {
            "date": day,
            "minutes": over.get(d, avail.get(d.weekday())),
            "is_override": d in over,
            "objective": chosen.get(d, "auto"),
        }

    @app.post("/api/calibrate")
    def calibrate_ep(session: DB, aid: AID, force: bool = True) -> dict[str, Any]:
        """Recalcula CP/W'/FTP ahora (botón de Ajustes)."""
        out = autocalibrate(session, aid, force=force)
        return {
            "ran": out.ran, "reason": out.reason,
            "ftp": round(out.ftp) if out.ftp else None,
            "cp": round(out.cp) if out.cp else None,
            "delta_ftp": round(out.delta_ftp) if out.delta_ftp else None,
        }

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

    @app.post("/api/chat/stream")
    def chat_stream_ep(body: ChatIn, session: DB, aid: AID) -> StreamingResponse:
        """Chat con respuesta en STREAMING (SSE): el texto llega por trozos y la
        app lo va escribiendo, en vez de esperar al mensaje completo."""
        key = str(aid)
        if key not in chat_state:
            try:
                chat_state[key] = ChatSession(aid, date.today(), LLMClient.from_settings())
            except LLMError as exc:
                raise HTTPException(503, str(exc)) from exc

        def events():
            try:
                for kind, payload in chat_state[key].turn_stream(session, body.message):
                    if kind == "done":
                        data = {"plan": _plan_json(payload.facts.plan)}
                    elif kind == "meta":
                        data = payload
                    else:
                        data = {"text": payload}
                    body_json = json.dumps(data, default=str)
                    yield f"event: {kind}\ndata: {body_json}\n\n"
            except LLMError as exc:
                err = json.dumps({"detail": str(exc)})
                yield f"event: error\ndata: {err}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
            "changed": reply.changed,
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

        @app.get("/sw.js")
        def service_worker() -> FileResponse:
            """El service worker se sirve desde la RAÍZ a propósito: uno servido
            bajo /static solo podría controlar /static, y la PWA necesita toda
            la app para ser instalable."""
            return FileResponse(
                _STATIC / "sw.js", media_type="application/javascript",
                headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-store"},
            )

        @app.get("/favicon.ico", include_in_schema=False)
        def favicon() -> FileResponse:
            """Los navegadores piden /favicon.ico en la raíz aunque el HTML
            declare otra ruta; sin esto se llevan un 404 y pintan el icono
            genérico."""
            return FileResponse(_STATIC / "favicon.ico", media_type="image/x-icon")

    return app


app = create_app()
