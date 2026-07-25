"""Servicio de planificación: reúne el estado del gemelo (FTP, TSB, CRI) y
produce la sesión recomendada de hoy."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from cycling_coach.db.repositories import latest_parameter_estimate
from cycling_coach.physiology import compute_ctl_atl_tsb
from cycling_coach.planner.planner import PlannedSession, plan_session
from cycling_coach.twin.cri_service import compute_cri_service
from cycling_coach.twin.load_service import daily_tss_series


def plan_today(session: Session, athlete_id: int, as_of: date) -> PlannedSession | None:
    """Sesión recomendada para `as_of`. None si falta el FTP (correr estimate-cp)."""
    ftp = latest_parameter_estimate(session, athlete_id, "ftp")
    if not ftp:
        return None

    tsb = ctl = atl = None
    daily = daily_tss_series(session, athlete_id, as_of)
    if daily:
        series = compute_ctl_atl_tsb(daily)
        if series:
            tsb, ctl, atl = series[-1].tsb, series[-1].ctl, series[-1].atl

    cri_detail = compute_cri_service(session, athlete_id, as_of)
    cri = cri_detail.result.cri if cri_detail else None

    return plan_session(ftp=ftp, tsb=tsb, ctl=ctl, atl=atl, cri=cri)
