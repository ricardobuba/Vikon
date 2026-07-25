"""Motor fisiológico (Fase 2) — modelos mecanísticos (white-box).

Empezamos por Critical Power (CP, W'), base para FTP y para el balance de W'.
Siguientes: Banister/Busso (fitness-fatiga), DFA-α1.
"""

from cycling_coach.physiology.cp_filter import (
    CPFilterConfig,
    CPObservation,
    CPState,
    CriticalPowerFilter,
    TestRecommendation,
    assess_test_need,
    build_cp_observations,
    run_cp_filter,
    run_cp_smoother,
)
from cycling_coach.physiology.critical_power import (
    CriticalPowerFit,
    fit_cp_wprime,
)

__all__ = [
    "CPFilterConfig",
    "CPObservation",
    "CPState",
    "CriticalPowerFilter",
    "CriticalPowerFit",
    "TestRecommendation",
    "assess_test_need",
    "build_cp_observations",
    "fit_cp_wprime",
    "run_cp_filter",
    "run_cp_smoother",
]
