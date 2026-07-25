"""Servicio del CRI: reúne rendimiento (CP), frescura (TSB) y tendencia (CTL)
de los datos reales y calcula el índice v1."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from cycling_coach.db.repositories import (
    latest_parameter_estimate,
    load_activity_loads,
    load_power_activities,
)
from cycling_coach.physiology import (
    build_cp_observations,
    compute_ctl_atl_tsb,
    run_cp_smoother,
    training_stress_score,
)
from cycling_coach.physiology.cri import (
    CRIResult,
    compute_cri,
    norm_freshness,
    norm_performance,
    norm_trend,
)
from cycling_coach.twin.cp_estimation import resolve_config


@dataclass
class CRIDetail:
    result: CRIResult
    current_cp: float
    tsb: float
    ctl: float


def compute_cri_service(
    session: Session, athlete_id: int, as_of: date
) -> CRIDetail | None:
    activities = load_power_activities(session, athlete_id)
    obs = build_cp_observations([(st, aid, d) for st, aid, d in activities])
    if not obs:
        return None
    cfg = resolve_config(session, athlete_id, None)
    traj = run_cp_smoother(obs, cfg)
    cps = np.array([s.cp.mean for s in traj])
    current_cp = float(cps[-1])
    performance = norm_performance(
        current_cp, float(np.percentile(cps, 10)), float(np.percentile(cps, 90))
    )

    ftp = latest_parameter_estimate(session, athlete_id, "ftp") or current_cp
    daily: dict[date, float] = defaultdict(float)
    for day, dur, np_w in load_activity_loads(session, athlete_id):
        daily[day] += training_stress_score(np_w, dur, ftp)
    daily.setdefault(as_of, 0.0)
    series = compute_ctl_atl_tsb(daily)
    tsb = series[-1].tsb
    ctl_now = series[-1].ctl
    ctl_prev = series[-43].ctl if len(series) > 43 else series[0].ctl

    components = {
        "performance": performance,
        "freshness": norm_freshness(tsb),
        "trend": norm_trend(ctl_now, ctl_prev),
        "recovery": None,        # HRV/sueño no ingestados aún
        "compliance": None,      # sin plan (Fase 3)
    }
    return CRIDetail(
        result=compute_cri(components),
        current_cp=current_cp,
        tsb=tsb,
        ctl=ctl_now,
    )
