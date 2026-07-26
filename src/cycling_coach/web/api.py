"""API JSON + servidor del frontend. Expone lo que el motor calcula; no decide
nada nuevo (reusa twin/planner/assistant). Pensada para reutilizarse desde una
app móvil el día de mañana."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from cycling_coach.assistant.assistant import ChatSession
from cycling_coach.assistant.grounding import Facts, gather_facts
from cycling_coach.assistant.llm import LLMClient, LLMError
from cycling_coach.db.engine import session_scope
from cycling_coach.db.models import Athlete
from cycling_coach.planner.planner import PlannedSession
from cycling_coach.planner.service import plan_horizon
from cycling_coach.sync import SyncError, sync_recent
from cycling_coach.twin.coherence_service import assess_cp_coherence

_STATIC = Path(__file__).parent / "static"


def _db() -> Iterator[Session]:
    with session_scope() as session:
        yield session


DB = Annotated[Session, Depends(_db)]


def _athlete_id(session: Session) -> int:
    aid = session.execute(select(Athlete.id).order_by(Athlete.id).limit(1)).scalar_one_or_none()
    if aid is None:
        raise HTTPException(404, "No hay atleta. Corre la ingesta primero.")
    return aid


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
        "plan": _plan_json(f.plan),
    }


class ChatIn(BaseModel):
    message: str


def create_app() -> FastAPI:
    app = FastAPI(title="Vikon", docs_url="/api/docs")
    chat_state: dict[str, ChatSession] = {}     # una conversación (single-user local)

    @app.get("/api/state")
    def state(session: DB) -> dict[str, Any]:
        aid = _athlete_id(session)
        facts = gather_facts(session, aid, date.today())
        return _facts_json(facts)

    @app.get("/api/horizon")
    def horizon(session: DB, days: int = 7) -> list[dict[str, Any]]:
        aid = _athlete_id(session)
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
            }
            for h in plan_horizon(session, aid, date.today(), days=days)
        ]

    @app.get("/api/coherence")
    def coherence(session: DB) -> dict[str, Any]:
        aid = _athlete_id(session)
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

    @app.post("/api/refresh")
    def refresh() -> dict[str, Any]:
        """Sincroniza las actividades nuevas de Strava (incremental). La llama la
        app al abrir → el plan refleja la salida de hoy sin backfill manual."""
        try:
            r = sync_recent(fetch_streams=True)
        except SyncError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {
            "new": r.activities_ingested,
            "streams": r.streams_ingested,
            "skipped": r.skipped_existing,
        }

    @app.post("/api/chat")
    def chat(body: ChatIn, session: DB) -> dict[str, Any]:
        aid = _athlete_id(session)
        try:
            if "s" not in chat_state:
                chat_state["s"] = ChatSession(aid, date.today(), LLMClient.from_settings())
            reply = chat_state["s"].turn(session, body.message)
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

    if _STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=_STATIC), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(_STATIC / "index.html")

    return app


app = create_app()
