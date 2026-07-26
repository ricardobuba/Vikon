"""Tests del planificador mínimo (estado → objetivo → sesión)."""

from __future__ import annotations

from datetime import date, timedelta

from cycling_coach.planner import (
    FormThresholds,
    Phase,
    RecentDay,
    TrainingContext,
    apply_constraints,
    apply_phase,
    choose_objective,
    phase_for,
    plan_session,
    render_targets,
)
from cycling_coach.planner.library import LIBRARY, Objective, select_template


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
    template = select_template(Objective.threshold, fitness_pct=0.5)   # umbral @ 95–100%
    lines = render_targets(template, ftp=300.0)
    # El bloque de intervalos al umbral: 95–100% de 300 = 285–300 W.
    assert any("285" in ln and "300" in ln for ln in lines)


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


# --- Grieta 3: umbrales personalizados --------------------------------------
def test_thresholds_fall_back_to_population_with_little_history():
    assert FormThresholds.personalize([0.0, -5.0]) == FormThresholds()


def test_thresholds_recenter_on_athlete_distribution():
    # Atleta de gran volumen: vive crónicamente fatigado (TSB −40..−1).
    history = [float(v) for v in range(-40, 0)] * 2   # 80 muestras (≥60)
    t = FormThresholds.personalize(history)
    # Los cortes bajan muy por debajo de los defaults poblacionales.
    assert t.recovery_below < -25
    assert t.endurance_below < -10


def test_high_volume_athlete_not_stuck_in_recovery():
    # TSB −20 sería "recuperar" con el corte −25... no: con SU distribución
    # es forma más neutra → estímulo, no descanso.
    history = [float(v) for v in range(-40, 0)] * 2
    t = FormThresholds.personalize(history)
    obj, _ = choose_objective(tsb=-20.0, thresholds=t)
    assert obj is not Objective.recovery


def test_personalization_flows_through_plan_session():
    history = [float(v) for v in range(-40, 0)] * 2   # ≥60 muestras
    ctx = TrainingContext(recent=[], tsb_history=history)
    # TSB −18: con defaults sería endurance; con su escala, algo más exigente.
    plan = plan_session(ftp=348.0, tsb=-18.0, cri=None, context=ctx)
    assert plan.objective in (Objective.sweet_spot, Objective.threshold)


# --- Grieta 4: escalera de dosis (progresión + variantes) -------------------
def test_families_have_ordered_dose_ladders():
    for obj, fam in LIBRARY.items():
        mins = [v.total_minutes() for v in fam.variants]
        assert mins == sorted(mins), f"{obj} desordenada"
        assert all(v.objective is obj for v in fam.variants)


def test_fitness_scales_the_dose():
    # Más en forma (percentil alto) ⇒ más dosis que menos en forma.
    easy = select_template(Objective.sweet_spot, fitness_pct=0.0)
    hard = select_template(Objective.sweet_spot, fitness_pct=1.0)
    assert hard.total_minutes() > easy.total_minutes()


def test_time_budget_caps_the_dose():
    # Con menos tiempo, baja de escalón aunque estés en forma.
    full = select_template(Objective.sweet_spot, fitness_pct=1.0)   # 4×15 ≈ 105'
    short = select_template(Objective.sweet_spot, fitness_pct=1.0, minutes=80)
    assert short.total_minutes() <= 80
    assert short.total_minutes() < full.total_minutes()


def test_time_budget_never_returns_empty():
    # Aunque el tope sea absurdamente bajo, siempre da la variante más corta.
    t = select_template(Objective.vo2max, fitness_pct=1.0, minutes=1)
    assert t is LIBRARY[Objective.vo2max].variants[0]


def test_dose_flows_through_plan_session():
    # Fresco + budget holgado para VO2 → cabe la variante corta (~62').
    ctx = TrainingContext(recent=[], fitness_pct=1.0)
    plan = plan_session(ftp=348.0, tsb=12.0, cri=75.0, context=ctx, minutes=65)
    assert plan.template.total_minutes() <= 65


def test_tight_budget_flags_the_note():
    # Budget imposible para calidad → da la más corta pero AVISA en el porqué.
    ctx = TrainingContext(recent=[], fitness_pct=1.0)
    plan = plan_session(ftp=348.0, tsb=12.0, cri=75.0, context=ctx, minutes=30)
    assert "excede" in plan.rationale


# --- Grieta 5: meta/evento → fase (con disciplina de confianza) --------------
def test_phase_boundaries():
    assert phase_for(None) is Phase.off
    assert phase_for(120) is Phase.base
    assert phase_for(60) is Phase.build
    assert phase_for(30) is Phase.peak
    assert phase_for(10) is Phase.taper
    assert phase_for(2) is Phase.race
    assert phase_for(-1) is Phase.off       # evento pasado


def test_base_and_build_do_not_cap_intensity():
    # DECISIÓN DE DISEÑO: lejos del evento NO imponemos periodización (baja
    # confianza) — la forma manda. VO2 sigue siendo VO2 en base/build.
    assert apply_phase(Objective.vo2max, Phase.base) == (Objective.vo2max, None)
    assert apply_phase(Objective.vo2max, Phase.build) == (Objective.vo2max, None)
    assert apply_phase(Objective.threshold, Phase.peak) == (Objective.threshold, None)


def test_race_week_eases_down():
    obj, note = apply_phase(Objective.vo2max, Phase.race)
    assert obj is Objective.recovery
    assert note and "carrera" in note


def test_taper_reduces_dose_not_objective():
    # En taper el objetivo NO cambia; baja el volumen (dosis).
    obj, note = apply_phase(Objective.threshold, Phase.taper)
    assert obj is Objective.threshold and note is None
    ctx = TrainingContext(recent=[], fitness_pct=1.0)
    normal = plan_session(ftp=348.0, tsb=12.0, cri=75.0, context=ctx)
    tapered = plan_session(
        ftp=348.0, tsb=12.0, cri=75.0, context=ctx,
        phase=Phase.taper, days_to_event=10,
    )
    assert tapered.template.total_minutes() < normal.template.total_minutes()


def test_phase_shown_in_rationale():
    plan = plan_session(ftp=348.0, tsb=0.0, phase=Phase.build, days_to_event=60)
    assert "fase build" in plan.rationale and "meta en 60" in plan.rationale
