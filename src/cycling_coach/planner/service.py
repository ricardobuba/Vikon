"""Servicio de planificación: reúne el estado del gemelo (FTP, TSB, CRI) y
produce la sesión recomendada de hoy."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from cycling_coach.db.repositories import latest_parameter_estimate
from cycling_coach.planner.planner import PlannedSession, plan_session
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

    return plan_session(
        ftp=ftp, tsb=tsb, ctl=ctl, atl=atl, cri=cri, context=ctx, minutes=minutes
    )
