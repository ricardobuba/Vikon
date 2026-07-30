"""Motor fisiológico (Fase 2) — modelos mecanísticos (white-box).

Empezamos por Critical Power (CP, W'), base para FTP y para el balance de W'.
Siguientes: Banister/Busso (fitness-fatiga), DFA-α1.
"""

from cycling_coach.physiology.backtest import BacktestResult, backtest_one_step
from cycling_coach.physiology.cp_filter import (
    CPFilterConfig,
    CPObservation,
    CPState,
    CriticalPowerFilter,
    TestRecommendation,
    assess_test_need,
    build_cp_observations,
    build_cp_observations_from_mmp,
    observation_from_activity,
    run_cp_filter,
    run_cp_smoother,
)
from cycling_coach.physiology.critical_power import (
    CriticalPowerFit,
    ThreeParamFit,
    fit_3param,
    fit_cp_wprime,
    three_param_power,
)
from cycling_coach.physiology.durability import (
    DurabilityFit,
    best_effort_with_context,
    estimate_durability,
)
from cycling_coach.physiology.training_load import (
    LoadPoint,
    compute_ctl_atl_tsb,
    hr_trimp_tss,
    training_stress_score,
)
from cycling_coach.physiology.tune import learn_hyperparameters

__all__ = [
    "BacktestResult",
    "CPFilterConfig",
    "DurabilityFit",
    "CPObservation",
    "CPState",
    "CriticalPowerFilter",
    "CriticalPowerFit",
    "LoadPoint",
    "TestRecommendation",
    "ThreeParamFit",
    "assess_test_need",
    "backtest_one_step",
    "best_effort_with_context",
    "build_cp_observations",
    "build_cp_observations_from_mmp",
    "compute_ctl_atl_tsb",
    "estimate_durability",
    "fit_3param",
    "fit_cp_wprime",
    "hr_trimp_tss",
    "three_param_power",
    "training_stress_score",
    "learn_hyperparameters",
    "observation_from_activity",
    "run_cp_filter",
    "run_cp_smoother",
]
