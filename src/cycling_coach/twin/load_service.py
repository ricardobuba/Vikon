"""Servicio de carga de entrenamiento: calcula TSS por sesión y las series
CTL/ATL/TSB, y persiste el estado actual en la capa `daily` del gemelo.

Refinos: (1) el TSS usa el FTP DE LA FECHA de cada actividad (trayectoria CP(t)),
no un FTP fijo — así la carga histórica está bien ponderada. (2) las actividades
CON pulso pero SIN potencia aportan carga vía TRIMP (no quedan invisibles).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from cycling_coach.db.models import Athlete
from cycling_coach.db.repositories import (
    estimate_hr_bounds,
    latest_parameter_estimate,
    load_activity_loads,
    load_hr_only_loads,
    load_power_activities,
    upsert_daily_metric,
)
from cycling_coach.domain.models import CanonicalDailyMetric
from cycling_coach.physiology import (
    build_cp_observations,
    compute_ctl_atl_tsb,
    hr_trimp_tss,
    run_cp_smoother,
    training_stress_score,
)
from cycling_coach.physiology.training_load import LoadPoint
from cycling_coach.planner.planner import (
    FormThresholds,
    RecentDay,
    TrainingContext,
    _weighted_fraction_below,
)
from cycling_coach.twin.cp_estimation import resolve_config


@dataclass
class LoadResult:
    current: LoadPoint
    n_days: int
    n_activities: int
    ftp: float


def ftp_trajectory(session: Session, athlete_id: int) -> list[tuple[date, float]]:
    """FTP(t) desde la trayectoria CP suavizada: [(fecha, ftp)] ordenada."""
    activities = load_power_activities(session, athlete_id)
    obs = build_cp_observations([(st, aid, d) for st, aid, d in activities])
    if not obs:
        return []
    cfg = resolve_config(session, athlete_id, None)
    return sorted((s.as_of.date(), s.ftp_w) for s in run_cp_smoother(obs, cfg))


def _ftp_asof(traj: list[tuple[date, float]], day: date, fallback: float) -> float:
    ftp = fallback
    for d, v in traj:
        if d <= day:
            ftp = v
        else:
            break
    return ftp


def daily_tss_series(
    session: Session, athlete_id: int, as_of: date
) -> dict[date, float] | None:
    """TSS diario: potencia (FTP de la fecha) + pulso (TRIMP) para las sin potencia."""
    ftp_traj = ftp_trajectory(session, athlete_id)
    fallback = latest_parameter_estimate(session, athlete_id, "ftp")
    if fallback is None and ftp_traj:
        fallback = ftp_traj[-1][1]

    daily: dict[date, float] = defaultdict(float)
    if fallback:
        for day, dur, np_w in load_activity_loads(session, athlete_id):
            daily[day] += training_stress_score(np_w, dur, _ftp_asof(ftp_traj, day, fallback))

    hr_bounds = estimate_hr_bounds(session, athlete_id)
    if hr_bounds:
        hr_rest, hr_max = hr_bounds
        athlete = session.get(Athlete, athlete_id)
        male = (athlete.sex or "M") != "F"
        for day, dur, avg_hr in load_hr_only_loads(session, athlete_id):
            daily[day] += hr_trimp_tss(avg_hr, dur, hr_rest, hr_max, male)

    if not daily:
        return None
    daily.setdefault(as_of, 0.0)
    return daily


def daily_load_and_intensity(
    session: Session, athlete_id: int, as_of: date
) -> dict[date, tuple[float, float]] | None:
    """dict[día] = (TSS total, intensidad máx del día).

    intensidad = IF real (NP/FTP) para sesiones con potencia; para las de solo
    pulso se deriva del TSS-equiv: TSS/h = 100·IF² ⇒ IF = √((TSS/h)/100).
    Base para la regla duro/fácil (grieta 1)."""
    ftp_traj = ftp_trajectory(session, athlete_id)
    fallback = latest_parameter_estimate(session, athlete_id, "ftp")
    if fallback is None and ftp_traj:
        fallback = ftp_traj[-1][1]

    tss_by_day: dict[date, float] = defaultdict(float)
    inten_by_day: dict[date, float] = defaultdict(float)
    if fallback:
        for day, dur, np_w in load_activity_loads(session, athlete_id):
            ftp = _ftp_asof(ftp_traj, day, fallback)
            tss_by_day[day] += training_stress_score(np_w, dur, ftp)
            if ftp > 0:
                inten_by_day[day] = max(inten_by_day[day], np_w / ftp)

    hr_bounds = estimate_hr_bounds(session, athlete_id)
    if hr_bounds:
        hr_rest, hr_max = hr_bounds
        athlete = session.get(Athlete, athlete_id)
        male = (athlete.sex or "M") != "F"
        for day, dur, avg_hr in load_hr_only_loads(session, athlete_id):
            tss = hr_trimp_tss(avg_hr, dur, hr_rest, hr_max, male)
            tss_by_day[day] += tss
            hours = dur / 3600.0
            if hours > 0 and tss > 0:
                inten_by_day[day] = max(inten_by_day[day], math.sqrt(tss / hours / 100.0))

    if not tss_by_day:
        return None
    tss_by_day.setdefault(as_of, 0.0)
    return {d: (tss_by_day[d], inten_by_day.get(d, 0.0)) for d in tss_by_day}


def build_training_context(
    session: Session, athlete_id: int, as_of: date, lookback_days: int = 10
) -> tuple[LoadPoint | None, TrainingContext | None]:
    """Estado de forma de hoy + contexto temporal (historia + ramp rate) para las
    restricciones de seguridad del planner. En una sola pasada de carga."""
    dli = daily_load_and_intensity(session, athlete_id, as_of)
    if dli is None:
        return None, None

    series = compute_ctl_atl_tsb({d: v[0] for d, v in dli.items()})
    by_day = {p.day: p for p in series}

    # Estado para PLANEAR hoy = mañana de `as_of` (fin de ayer), ANTES del
    # entreno de hoy. `LoadPoint(as_of)` guarda ctl/atl de FIN de día; usarlos
    # como base subestimaría la fatiga (un día extra de decaimiento) y haría que
    # el TSB del horizonte no cuadrara con el de `cc plan`. Tomamos el fin de
    # ayer, cuyo ctl−atl es exactamente el TSB matinal de hoy.
    end_of_day = by_day.get(as_of, series[-1])
    prev = by_day.get(as_of - timedelta(days=1))
    if prev is not None:
        current = LoadPoint(
            day=as_of, ctl=prev.ctl, atl=prev.atl, tsb=prev.ctl - prev.atl
        )
    else:
        current = end_of_day

    week_ago = by_day.get(as_of - timedelta(days=8))    # 7 días antes (mañana→mañana)
    ramp = (current.ctl - week_ago.ctl) if week_ago else None
    acwr = (current.atl / current.ctl) if current.ctl > 0 else None

    recent: list[RecentDay] = []
    for i in range(lookback_days, 0, -1):          # de más viejo a ayer
        d = as_of - timedelta(days=i)
        tss, inten = dli.get(d, (0.0, 0.0))
        recent.append(RecentDay(day=d, tss=tss, intensity=inten))

    # TODA la historia (no solo el último año): umbrales de forma (grieta 3) y
    # forma relativa para la dosis (grieta 4). El atleta puede haber cambiado de
    # volumen entre años; usar todo capta su capacidad real de largo plazo.
    past = [p for p in series if p.day <= as_of]
    tsb_history = [p.tsb for p in past]                 # viejo→nuevo (contiguo)
    ctl_series = [p.ctl for p in past]                  # viejo→nuevo (contiguo)
    fitness_pct = None
    if ctl_series:
        fitness_pct = _weighted_fraction_below(
            ctl_series, current.ctl, FormThresholds.HALFLIFE_DAYS
        )

    # Últimos ~8 CTL hasta fin de ayer (coherente con la base matinal): ramp
    # rate durante el rollout del horizonte.
    ctl_window = [p.ctl for p in past if p.day < as_of][-8:]

    return current, TrainingContext(
        ramp_rate=ramp,
        acwr=acwr,
        recent=recent,
        tsb_history=tsb_history,
        fitness_pct=fitness_pct,
        ctl_window=ctl_window,
    )


def compute_and_store_load(
    session: Session, athlete_id: int, as_of: date
) -> LoadResult | None:
    """Calcula CTL/ATL/TSB hasta `as_of` (FTP variable + TRIMP) y guarda el
    estado actual en daily_metric."""
    daily = daily_tss_series(session, athlete_id, as_of)
    if daily is None:
        return None
    series = compute_ctl_atl_tsb(daily)
    last = series[-1]
    for metric, value in (("ctl", last.ctl), ("atl", last.atl), ("tsb", last.tsb)):
        upsert_daily_metric(
            session,
            athlete_id,
            CanonicalDailyMetric(metric=metric, day=last.day, value=value, source="computed"),
        )
    n_act = sum(1 for d in daily if daily[d] > 0)
    ftp = latest_parameter_estimate(session, athlete_id, "ftp") or 0.0
    return LoadResult(current=last, n_days=len(series), n_activities=n_act, ftp=ftp)
