"""Tests del planificador mínimo (estado → objetivo → sesión)."""

from __future__ import annotations

from datetime import date, timedelta

from cycling_coach.planner import (
    RecentDay,
    TrainingContext,
    apply_constraints,
    choose_objective,
    plan_session,
    render_targets,
)
from cycling_coach.planner.library import LIBRARY, Objective


def _ctx(intensities: list[float], ramp=None, acwr=None, tss=None) -> TrainingContext:
    """Contexto con `intensities` de más viejo a ayer (tss paralelo opcional)."""
    base = date(2026, 7, 20)
    tss = tss or [0.0] * len(intensities)
    recent = [
        RecentDay(day=base + timedelta(days=i), tss=tss[i], intensity=inten)
        for i, inten in enumerate(intensities)
    ]
    return TrainingContext(ramp_rate=ramp, acwr=acwr, recent=recent)


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


# --- Grietas 1+2: capa de seguridad -----------------------------------------
def test_yesterday_hard_downgrades_intensity():
    # Aspira a VO2máx (fresco) pero ayer fue día duro → regla duro/fácil.
    ctx = _ctx([0.6, 0.6, 0.95])          # ayer IF 0.95 = duro
    obj, note = apply_constraints(Objective.vo2max, ctx)
    assert obj is Objective.endurance
    assert note and "duro" in note


def test_no_downgrade_when_history_easy():
    ctx = _ctx([0.6, 0.0, 0.65])          # nada duro
    obj, note = apply_constraints(Objective.vo2max, ctx)
    assert obj is Objective.vo2max
    assert note is None


def test_vo2_not_given_every_day():
    # El fallo concreto de la grieta 1: fresco + VO2 ayer → hoy NO otro VO2.
    ctx = _ctx([0.0, 0.0, 0.88])
    plan = plan_session(ftp=348.0, tsb=12.0, cri=75.0, context=ctx)
    assert plan.objective is not Objective.vo2max
    assert plan.aspired is Objective.vo2max


def test_high_ramp_rate_caps_intensity():
    ctx = _ctx([0.6, 0.6, 0.6], ramp=12.0)   # +12 CTL/sem = agresivo
    obj, note = apply_constraints(Objective.threshold, ctx)
    assert obj is Objective.endurance
    assert note and "CTL/sem" in note


def test_high_acwr_forces_recovery():
    ctx = _ctx([0.6, 0.6, 0.7], acwr=1.8)
    obj, note = apply_constraints(Objective.threshold, ctx)
    assert obj is Objective.recovery
    assert note and "ACWR" in note


def test_weekly_hard_quota_caps_at_sweet_spot():
    # 3 días duros en la ventana → no más calidad de umbral/VO2 (techo SS).
    ctx = _ctx([0.9, 0.9, 0.9, 0.6, 0.6, 0.6, 0.6])
    obj, _ = apply_constraints(Objective.threshold, ctx)
    assert obj is Objective.sweet_spot


def test_big_volume_day_counts_as_hard():
    # Día largo Z2 (IF bajo) pero TSS alto → cuenta como duro para el espaciado.
    ctx = _ctx([0.6, 0.6, 0.7], tss=[0.0, 0.0, 200.0])
    assert ctx.recent[-1].is_hard
