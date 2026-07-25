"""Tests del planificador mínimo (estado → objetivo → sesión)."""

from __future__ import annotations

from cycling_coach.planner import choose_objective, plan_session, render_targets
from cycling_coach.planner.library import LIBRARY, Objective


def test_objective_by_form():
    assert choose_objective(tsb=-30)[0] is Objective.recovery
    assert choose_objective(tsb=-15)[0] is Objective.endurance
    assert choose_objective(tsb=0)[0] is Objective.sweet_spot
    assert choose_objective(tsb=10)[0] is Objective.threshold
    assert choose_objective(tsb=10, cri=80)[0] is Objective.vo2max
    assert choose_objective(tsb=None)[0] is Objective.endurance


def test_low_cri_forces_recovery_even_if_form_ok():
    assert choose_objective(tsb=0, cri=30)[0] is Objective.recovery


def test_render_targets_scales_to_ftp():
    template = LIBRARY[Objective.threshold]        # 3×10' @ 95–100%
    lines = render_targets(template, ftp=300.0)
    # El bloque de intervalos: 95–100% de 300 = 285–300 W.
    assert any("285" in ln and "300" in ln and "3×10" in ln for ln in lines)


def test_plan_session_produces_rationale_and_targets():
    plan = plan_session(ftp=348.0, tsb=12.0, cri=75.0)
    assert plan.objective is Objective.vo2max
    assert "VO2" in plan.template.name or "vo2" in plan.rationale.lower()
    assert plan.targets and all(" W" in t for t in plan.targets)
    assert plan.template.total_minutes() > 0
