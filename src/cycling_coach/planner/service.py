"""Servicio de planificación: reúne el estado del gemelo (FTP, TSB, CRI) y
produce la sesión recomendada de hoy."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from cycling_coach.db.repositories import latest_parameter_estimate, next_goal
from cycling_coach.planner.planner import (
    HorizonDay,
    PlannedSession,
    phase_for,
    plan_session,
    roll_horizon,
)
from cycling_coach.twin.cri_service import compute_cri_service
from cycling_coach.twin.load_service import build_training_context


def plan_today(
    session: Session, athlete_id: int, as_of: date, minutes: float | None = None
) -> PlannedSession | None:
    """Sesión recomendada para `as_of`. None si falta el FTP (correr estimate-cp).

    Reúne el estado de forma (TSB/CTL/ATL) Y el contexto temporal (historia
    completa + ramp rate + forma relativa) en una sola pasada, para que la capa
    de seguridad (grietas 1+2), los umbrales personalizados (grieta 3) y la
    selección de dosis (grieta 4) tengan todo lo que necesitan."""
    ftp = latest_parameter_estimate(session, athlete_id, "ftp")
    if not ftp:
        return None

    current, ctx = build_training_context(session, athlete_id, as_of)
    tsb = current.tsb if current else None
    ctl = current.ctl if current else None
    atl = current.atl if current else None

    cri_detail = compute_cri_service(session, athlete_id, as_of)
    cri = cri_detail.result.cri if cri_detail else None

    # Horizonte: si hay un evento futuro, su cercanía define la fase (grieta 5).
    goal = next_goal(session, athlete_id, as_of)
    days_to_event = (goal.event_date - as_of).days if goal else None
    phase = phase_for(days_to_event)

    return plan_session(
        ftp=ftp, tsb=tsb, ctl=ctl, atl=atl, cri=cri, context=ctx,
        minutes=minutes, phase=phase, days_to_event=days_to_event,
    )


def plan_horizon(
    session: Session,
    athlete_id: int,
    as_of: date,
    days: int = 7,
    minutes: float | None = None,
) -> list[HorizonDay]:
    """Microciclo proyectado: rollout simulado de `days` días desde `as_of`.

    Solo el día 0 se compromete; el resto se re-planifica al llegar datos reales
    (horizonte deslizante). [] si falta FTP o estado de carga."""
    ftp = latest_parameter_estimate(session, athlete_id, "ftp")
    if not ftp:
        return []
    current, ctx = build_training_context(session, athlete_id, as_of)
    if current is None or ctx is None:
        return []

    cri_detail = compute_cri_service(session, athlete_id, as_of)
    cri = cri_detail.result.cri if cri_detail else None

    goal = next_goal(session, athlete_id, as_of)
    days_to_event = (goal.event_date - as_of).days if goal else None

    return roll_horizon(
        ftp=ftp, ctl=current.ctl, atl=current.atl, context=ctx, cri=cri,
        days=days, start=as_of, days_to_event=days_to_event, minutes=minutes,
    )
