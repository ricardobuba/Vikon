"""Estimación de CP/W'/FTP actuales fusionando actividades + tests de campo.

Los tests introducidos por el usuario entran como observaciones de ALTA
confianza (sd pequeña) que anclan el filtro. Lógica compartida por los comandos
`estimate-cp` y `add-test`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from cycling_coach.db.repositories import (
    load_marked_test_activities,
    load_power_activities,
    load_test_results,
)
from cycling_coach.physiology import (
    BacktestResult,
    CPFilterConfig,
    CPObservation,
    CPState,
    TestRecommendation,
    assess_test_need,
    backtest_one_step,
    build_cp_observations,
    observation_from_activity,
    run_cp_filter,
)

_WPRIME_NOMINAL = 20000.0
_SD_WP_IGNORE = 1.0e6      # sd enorme → el filtro no actualiza W' con ese punto


@dataclass
class CPEstimationResult:
    state: CPState
    recommendation: TestRecommendation
    n_activity_obs: int
    n_test_obs: int          # tests manuales (add-test) + actividades marcadas (mark-test)


def _test_observations(session: Session, athlete_id: int) -> list[CPObservation]:
    obs: list[CPObservation] = []
    for t in load_test_results(session, athlete_id):
        obs.append(
            CPObservation(
                when=t.date,
                cp=t.cp,
                w_prime=t.w_prime if t.w_prime is not None else _WPRIME_NOMINAL,
                sd_cp=t.sd_cp,
                sd_wp=t.sd_wp if t.sd_wp is not None else _SD_WP_IGNORE,
            )
        )
    return obs


def estimate_cp(
    session: Session, athlete_id: int, config: CPFilterConfig | None = None
) -> CPEstimationResult | None:
    """Estima CP/W'/FTP actuales. Devuelve None si no hay observaciones."""
    activities = load_power_activities(session, athlete_id)
    activity_obs = build_cp_observations(activities)

    # Anclas: tests manuales (add-test) + actividades marcadas maximales (mark-test).
    test_obs = _test_observations(session, athlete_id)
    for when, _aid, watts in load_marked_test_activities(session, athlete_id):
        marked = observation_from_activity(when, watts)
        if marked is not None:
            test_obs.append(marked)

    all_obs = sorted(activity_obs + test_obs, key=lambda o: o.when)
    if not all_obs:
        return None

    trajectory = run_cp_filter(all_obs, config=config or CPFilterConfig())
    current = trajectory[-1]
    rec = assess_test_need(current, all_obs[-1].when, current.as_of)
    return CPEstimationResult(
        state=current,
        recommendation=rec,
        n_activity_obs=len(activity_obs),
        n_test_obs=len(test_obs),
    )


def backtest(
    session: Session,
    athlete_id: int,
    config: CPFilterConfig | None = None,
    window_days: int = 42,
) -> BacktestResult | None:
    """Backtest one-step-ahead sobre observaciones NO solapadas (stride=ventana,
    para no filtrar información entre observaciones adyacentes)."""
    activities = load_power_activities(session, athlete_id)
    obs = build_cp_observations(
        activities, window_days=window_days, stride_days=window_days
    )
    return backtest_one_step(obs, config=config or CPFilterConfig())
