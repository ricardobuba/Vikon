"""Servicio del CRI: reúne rendimiento (CP), frescura (TSB) y tendencia (CTL) de
los datos reales, calcula el índice y CALIBRA los pesos contra el rendimiento."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from sqlalchemy.orm import Session

from cycling_coach.db.repositories import (
    latest_daily_metric,
    load_cri_weights,
    load_power_activities,
    save_cri_weights,
)
from cycling_coach.metrics import mean_maximal_power
from cycling_coach.metrics.cleaning import clean_power
from cycling_coach.physiology import (
    compute_ctl_atl_tsb,
)
from cycling_coach.physiology.cri import (
    CRICalibration,
    CRIResult,
    calibrate_weights,
    compute_cri,
    norm_freshness,
    norm_performance,
    norm_recovery,
    norm_trend,
)
from cycling_coach.twin.load_service import daily_tss_series, smoothed_cp_states


@dataclass
class CRIDetail:
    result: CRIResult
    current_cp: float
    tsb: float
    ctl: float


@dataclass
class _Context:
    cp_at: list[tuple[date, float]]      # trayectoria CP (fecha, cp), ordenada
    cp_low: float
    cp_high: float
    ctl_by_day: dict[date, float]
    tsb_by_day: dict[date, float]


def _build_context(session: Session, athlete_id: int, as_of: date) -> _Context | None:
    traj = smoothed_cp_states(session, athlete_id)   # caché compartida (evita recomputar)
    if not traj:
        return None
    cps = np.array([s.cp.mean for s in traj])
    cp_at = sorted((s.as_of.date(), s.cp.mean) for s in traj)

    daily = daily_tss_series(session, athlete_id, as_of) or {as_of: 0.0}
    series = compute_ctl_atl_tsb(daily)
    return _Context(
        cp_at=cp_at,
        cp_low=float(np.percentile(cps, 10)),
        cp_high=float(np.percentile(cps, 90)),
        ctl_by_day={p.day: p.ctl for p in series},
        tsb_by_day={p.day: p.tsb for p in series},
    )


def _cp_asof(cp_at: list[tuple[date, float]], day: date) -> float:
    cp = cp_at[0][1]
    for d, v in cp_at:
        if d <= day:
            cp = v
        else:
            break
    return cp


def _components_asof(ctx: _Context, day: date) -> dict[str, float] | None:
    if day not in ctx.tsb_by_day:
        return None
    cp = _cp_asof(ctx.cp_at, day)
    ctl = ctx.ctl_by_day[day]
    ctl_prev = ctx.ctl_by_day.get(day - timedelta(days=42), ctl)
    return {
        "performance": norm_performance(cp, ctx.cp_low, ctx.cp_high),
        "freshness": norm_freshness(ctx.tsb_by_day[day]),
        "trend": norm_trend(ctl, ctl_prev),
    }


def compute_cri_service(
    session: Session, athlete_id: int, as_of: date
) -> CRIDetail | None:
    ctx = _build_context(session, athlete_id, as_of)
    if ctx is None:
        return None
    comps = _components_asof(ctx, as_of) or {
        "performance": norm_performance(
            _cp_asof(ctx.cp_at, as_of), ctx.cp_low, ctx.cp_high
        ),
        "freshness": 0.5,
        "trend": 0.5,
    }
    # Recuperación desde el check-in manual reciente (sueño + sensación), si lo hay.
    sleep = latest_daily_metric(session, athlete_id, "sleep_hours", as_of)
    feel = latest_daily_metric(session, athlete_id, "readiness", as_of)
    recent = timedelta(days=3)
    sleep_h = sleep[1] if sleep and (as_of - sleep[0]) <= recent else None
    feel_v = feel[1] if feel and (as_of - feel[0]) <= recent else None
    comps["recovery"] = norm_recovery(sleep_hours=sleep_h, feel=feel_v)
    comps["compliance"] = None    # sin plan (Fase 3)
    weights = load_cri_weights(session, athlete_id)
    return CRIDetail(
        result=compute_cri(comps, weights=weights),
        current_cp=_cp_asof(ctx.cp_at, as_of),
        tsb=ctx.tsb_by_day.get(as_of, 0.0),
        ctl=ctx.ctl_by_day.get(as_of, 0.0),
    )


def calibrate_cri(
    session: Session, athlete_id: int, as_of: date, save: bool = True
) -> CRICalibration | None:
    """Aprende los pesos del CRI: para cada día con un esfuerzo maximal (mejor
    20-min alto), (componentes ANTES de ese día, rendimiento observado ese día)."""
    ctx = _build_context(session, athlete_id, as_of)
    if ctx is None:
        return None

    activities = load_power_activities(session, athlete_id)
    p20: list[tuple[date, float]] = []
    for st, _aid, watts in activities:
        power = mean_maximal_power(clean_power(watts), durations_s=[1200]).get(1200)
        if power and 120 <= power <= 500:      # filtra contaminación
            p20.append((st.date(), power))
    if not p20:
        return None
    best20 = max(p for _, p in p20)
    powers = np.array([p for _, p in p20])
    lo, hi = float(np.percentile(powers, 10)), float(np.percentile(powers, 90))

    samples: list[tuple[dict[str, float], float]] = []
    for day, power in p20:
        if power < 0.85 * best20:              # solo esfuerzos duros (maximales)
            continue
        comps = _components_asof(ctx, day)
        if comps is None:
            continue
        outcome = float(np.clip((power - lo) / (hi - lo), 0.0, 1.0)) if hi > lo else 0.5
        samples.append((comps, outcome))

    cal = calibrate_weights(samples)
    if cal is not None and save:
        # Guardar solo si mejora de verdad; si no, limpiar (volver a defaults).
        save_cri_weights(session, athlete_id, cal.weights if cal.improved else None)
    return cal
