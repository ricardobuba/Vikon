"""Servicio de planificación: reúne el estado del gemelo (FTP, TSB, CRI) y
produce la sesión recomendada de hoy."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from sqlalchemy.orm import Session

from cycling_coach.db.repositories import (
    get_athlete,
    get_availability,
    get_availability_overrides,
    get_plan_overrides,
    latest_parameter_estimate,
    next_goal,
)
from cycling_coach.planner.planner import (
    STACKING_EVENTS,
    HorizonDay,
    PlannedSession,
    phase_for,
    plan_session,
    rest_session,
    roll_horizon,
)
from cycling_coach.twin.cri_service import compute_cri_service
from cycling_coach.twin.load_service import build_training_context


def plan_today(
    session: Session,
    athlete_id: int,
    as_of: date,
    minutes: float | None = None,
    cri_override: float | None = None,
) -> PlannedSession | None:
    """Sesión recomendada para `as_of`. None si falta el FTP (correr estimate-cp).

    Reúne el estado de forma (TSB/CTL/ATL) Y el contexto temporal (historia
    completa + ramp rate + forma relativa) en una sola pasada, para que la capa
    de seguridad (grietas 1+2), los umbrales personalizados (grieta 3) y la
    selección de dosis (grieta 4) tengan todo lo que necesitan.

    `cri_override`: disposición subjetiva (0–100) que la capa conversacional
    deriva de cómo dices sentirte hoy — sustituye al CRI calculado, de forma
    determinista (el LLM traduce; el planner decide)."""
    ftp = latest_parameter_estimate(session, athlete_id, "ftp")
    if not ftp:
        return None

    # Disponibilidad de HOY (por día de la semana): 0 = descanso; si no está
    # configurada, se respeta el `minutes` que llegue (o ninguno).
    avail = get_availability(session, athlete_id)
    if avail and as_of.weekday() in avail:
        minutes = avail[as_of.weekday()]
    # Excepción puntual de ESE día (manda sobre la semanal).
    over = get_availability_overrides(session, athlete_id, as_of, as_of)
    if as_of in over:
        minutes = over[as_of]
    if minutes is not None and minutes <= 0:
        return rest_session(ftp, "descanso: tu disponibilidad de hoy es 0 min")

    current, ctx = build_training_context(session, athlete_id, as_of)
    tsb = current.tsb if current else None
    ctl = current.ctl if current else None
    atl = current.atl if current else None

    if cri_override is not None:
        cri = cri_override
    else:
        cri_detail = compute_cri_service(session, athlete_id, as_of)
        cri = cri_detail.result.cri if cri_detail else None

    # Horizonte: si hay un evento futuro, su cercanía define la fase (grieta 5) y
    # su TIPO sesga el énfasis de la calidad (crono→FTP, gran fondo→aeróbico...).
    goal = next_goal(session, athlete_id, as_of)
    days_to_event = (goal.event_date - as_of).days if goal else None
    phase = phase_for(days_to_event)

    # La regla duro/fácil se relaja si entrenas pocos días o el evento pide
    # bloques (días consecutivos) — ver TrainingContext.allows_back_to_back.
    if ctx is not None:
        ctx = replace(
            ctx,
            available_days=(sum(1 for v in avail.values() if v > 0) if avail else None),
            stack_hard=bool(goal and goal.kind in STACKING_EVENTS),
        )

    return plan_session(
        ftp=ftp, tsb=tsb, ctl=ctl, atl=atl, cri=cri, context=ctx,
        minutes=minutes, phase=phase, days_to_event=days_to_event,
        event_kind=goal.kind if goal else None,
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

    avail = get_availability(session, athlete_id)
    overrides = get_availability_overrides(
        session, athlete_id, as_of, as_of + timedelta(days=days)
    )
    chosen = get_plan_overrides(
        session, athlete_id, as_of, as_of + timedelta(days=days)
    )
    athlete = get_athlete(session, athlete_id)
    return roll_horizon(
        ftp=ftp, ctl=current.ctl, atl=current.atl, context=ctx, cri=cri,
        days=days, start=as_of, days_to_event=days_to_event, minutes=minutes,
        daily_minutes=avail or None, date_minutes=overrides or None,
        date_objective=chosen or None,
        event_kind=goal.kind if goal else None,
        # Cuántas horas QUIERE entrenar a la semana. Se recogía en el
        # onboarding y no la usaba nadie: la disponibilidad es el techo por día,
        # esto es el presupuesto del total.
        weekly_minutes=(athlete.weekly_minutes_target if athlete else None),
    )
