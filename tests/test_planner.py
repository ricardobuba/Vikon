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
    roll_horizon,
)
from cycling_coach.planner.library import LIBRARY, Objective, select_template
from cycling_coach.planner.planner import INTENSITY_RANK
from cycling_coach.planner.simulator import session_intensity

_HARD = {Objective.sweet_spot, Objective.threshold, Objective.vo2max}


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


def test_recency_weighting_shifts_toward_recent_regime():
    # Régimen viejo fresco (TSB +10), reciente fatigado (TSB −10). Con decaimiento
    # los umbrales se recentran hacia el régimen ACTUAL.
    hist = [10.0] * 200 + [-10.0] * 200            # viejo→nuevo
    equalish = FormThresholds.personalize(hist, halflife_days=1e9)
    recent = FormThresholds.personalize(hist, halflife_days=40)
    assert recent.sweet_below < equalish.sweet_below
    assert recent.endurance_below <= equalish.endurance_below


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


# --- Grieta 6: la simulación guía la dosis en plan_session -------------------
def test_simulation_engages_when_state_known():
    # Con CTL/ATL, la dosis se elige por simulación y se explica.
    plan = plan_session(ftp=348.0, tsb=8.0, ctl=60.0, atl=52.0, cri=60.0)
    assert "simulado" in plan.rationale and "mañana TSB" in plan.rationale


def test_simulation_prefers_bigger_stimulus_when_fresh():
    # Muy fresco (poca fatiga) → mayor dosis que ya fatigado, mismo objetivo.
    fresh = plan_session(ftp=348.0, tsb=6.0, ctl=60.0, atl=45.0, cri=60.0)
    tired = plan_session(ftp=348.0, tsb=6.0, ctl=60.0, atl=70.0, cri=60.0)
    assert fresh.template.total_minutes() >= tired.template.total_minutes()


def test_no_simulation_without_state():
    # Sin CTL/ATL, cae al heurístico y no habla de simulación.
    plan = plan_session(ftp=348.0, tsb=8.0, cri=60.0)
    assert "simulado" not in plan.rationale


# --- G7: horizonte deslizante -----------------------------------------------
def _horizon_ctx() -> TrainingContext:
    return TrainingContext(recent=[], fitness_pct=1.0, tsb_history=[], ctl_window=[60.0] * 8)


def test_horizon_length_and_consecutive_dates():
    h = roll_horizon(
        ftp=348.0, ctl=55.0, atl=50.0, context=_horizon_ctx(),
        cri=None, days=7, start=date(2026, 7, 26),
    )
    assert len(h) == 7
    assert [d.day for d in h] == [date(2026, 7, 26) + timedelta(days=i) for i in range(7)]


def test_horizon_state_evolves():
    h = roll_horizon(
        ftp=348.0, ctl=50.0, atl=40.0, context=_horizon_ctx(),
        days=5, start=date(2026, 7, 26),
    )
    assert len({round(d.ctl, 3) for d in h}) > 1     # el CTL cambia día a día
    assert all(d.tss >= 0 for d in h)


def test_horizon_alternates_hard_easy():
    # La alternancia EMERGE del simulador + regla duro/fácil: nunca 2 días duros
    # seguidos (tras un duro, el estado y la historia fuerzan uno suave).
    h = roll_horizon(
        ftp=348.0, ctl=60.0, atl=40.0, context=_horizon_ctx(),
        cri=80.0, days=7, start=date(2026, 7, 26),
    )
    hard = [session_intensity(d.plan.template) >= 0.85 for d in h]
    assert any(hard)                                  # hay días de calidad
    assert not any(hard[i] and hard[i + 1] for i in range(len(hard) - 1))


def test_emergent_rest_when_even_recovery_digs_below_floor():
    # Muy fatigado (ATL >> CTL): ni el rodaje suave deja mañana sobre el suelo
    # (default −25) → descanso total.
    ctx = TrainingContext(recent=[], fitness_pct=0.5)
    plan = plan_session(ftp=348.0, tsb=-50.0, ctl=40.0, atl=90.0, cri=30.0, context=ctx)
    assert plan.objective is Objective.rest
    assert plan.template.total_minutes() == 0
    assert not plan.targets
    assert "descanso" in plan.rationale.lower()
    assert plan.aspired is None          # no es un rebaje de intensidad


def test_recovery_stays_when_it_keeps_you_safe():
    # Fatigado pero recovery mantiene la forma sobre el suelo → recovery, no rest.
    ctx = TrainingContext(recent=[], fitness_pct=0.5)
    plan = plan_session(ftp=348.0, tsb=-30.0, ctl=55.0, atl=60.0, cri=35.0, context=ctx)
    assert plan.objective is Objective.recovery


def test_horizon_can_surface_rest_days():
    # Arrancando hundido, el rollout debe incluir al menos un descanso.
    ctx = TrainingContext(recent=[], fitness_pct=0.3, tsb_history=[], ctl_window=[45.0] * 8)
    h = roll_horizon(
        ftp=348.0, ctl=42.0, atl=95.0, context=ctx, cri=30.0,
        days=5, start=date(2026, 7, 26),
    )
    assert any(d.plan.objective is Objective.rest for d in h)
    # Tras descansar, el estado se recupera (ATL baja, TSB sube).
    assert h[-1].tsb > h[0].tsb


def test_horizon_tapers_toward_event():
    # Con un evento cercano, los últimos días entran en taper/carrera → menos
    # carga total que sin evento.
    far = roll_horizon(
        ftp=348.0, ctl=60.0, atl=45.0, context=_horizon_ctx(),
        cri=70.0, days=7, start=date(2026, 7, 26), days_to_event=None,
    )
    near = roll_horizon(
        ftp=348.0, ctl=60.0, atl=45.0, context=_horizon_ctx(),
        cri=70.0, days=7, start=date(2026, 7, 26), days_to_event=8,
    )
    assert sum(d.tss for d in near) < sum(d.tss for d in far)
    assert near[-1].phase in (Phase.race, Phase.taper)


# --- El TIPO de evento adapta el énfasis de la calidad -----------------------
def test_event_quality_menu_rotates_and_differs_by_event():
    from cycling_coach.planner.planner import event_quality

    # Cada evento rota su propio menú (variedad, no un único estímulo).
    crit = [event_quality("criterium", i) for i in range(3)]
    crono = [event_quality("crono", i) for i in range(3)]
    fondo = [event_quality("gran_fondo", i) for i in range(3)]
    assert len(set(crit)) > 1 and len(set(crono)) > 1     # hay mezcla
    assert Objective.vo2max in crit                       # criterium pide top-end
    assert crono.count(Objective.threshold) >= 2          # crono manda FTP
    assert Objective.vo2max not in fondo                  # gran fondo, sin top-end
    # Rota (índice cíclico), no se queda clavado.
    assert event_quality("criterium", 0) is event_quality("criterium", 3)


def test_horizon_emphasis_depends_on_event_kind():
    # Atleta con algo de fatiga (la forma pide base) → los días de calidad los
    # decide la "calidad garantizada", y ahí manda el TIPO de evento.
    ctx = TrainingContext(
        recent=[], fitness_pct=0.7, tsb_history=[],
        ctl_window=[50.0] * 8, days_since_quality=5,
    )

    def objectives(kind):
        return [
            d.plan.objective for d in roll_horizon(
                ftp=348.0, ctl=50.0, atl=65.0, context=ctx,
                days=10, start=date(2026, 7, 26), event_kind=kind,
            )
        ]

    crit, fondo = objectives("criterium"), objectives("gran_fondo")
    assert crit != fondo                                   # el evento importa
    assert Objective.vo2max in crit                        # criterium: top-end
    assert Objective.vo2max not in fondo                   # gran fondo: sin VO2
    # Todos conservan la base aeróbica (la resistencia no se sacrifica).
    assert Objective.endurance in crit and Objective.endurance in fondo


# --- Duro/fácil es una GUÍA, no una ley -------------------------------------
def _hard_yday() -> list[RecentDay]:
    return [RecentDay(date(2026, 7, 25), 90.0, 0.95)]      # ayer, duro


def test_hard_easy_blocks_by_default():
    # Con disponibilidad amplia y sin evento de bloques, tras un día duro se baja.
    ctx = TrainingContext(recent=_hard_yday(), available_days=6)
    obj, why = apply_constraints(Objective.threshold, ctx)
    assert obj is Objective.endurance and "duro/fácil" in why


def test_low_availability_allows_back_to_back():
    # Si solo entrenas 3 días, separar siempre la calidad = no entrenarla nunca.
    ctx = TrainingContext(recent=_hard_yday(), available_days=3)
    obj, why = apply_constraints(Objective.threshold, ctx)
    assert obj is Objective.threshold and why is None


def test_event_specificity_allows_back_to_back():
    # Eventos que exigen rendir en días consecutivos (bloques).
    ctx = TrainingContext(recent=_hard_yday(), available_days=6, stack_hard=True)
    obj, _ = apply_constraints(Objective.threshold, ctx)
    assert obj is Objective.threshold


def test_never_three_hard_in_a_row():
    # Dos duros seguidos ya es el límite: el tercero SIEMPRE se rebaja.
    two = [
        RecentDay(date(2026, 7, 24), 90.0, 0.95),
        RecentDay(date(2026, 7, 25), 90.0, 0.95),
    ]
    for ctx in (
        TrainingContext(recent=two, available_days=2),
        TrainingContext(recent=two, available_days=2, stack_hard=True),
    ):
        assert ctx.allows_back_to_back() is False
        obj, _ = apply_constraints(Objective.threshold, ctx)
        assert INTENSITY_RANK[obj] < INTENSITY_RANK[Objective.sweet_spot]


# --- Semana de descarga (deload) con presupuesto SEMANAL de carga -----------
def _loaded_ctx() -> TrainingContext:
    """Atleta que lleva semanas construyendo (CTL al alza) con carga real."""
    recent = [
        RecentDay(date(2026, 7, 25) + timedelta(days=i), 80.0, 0.7) for i in range(7)
    ]
    return TrainingContext(
        recent=recent, fitness_pct=0.5, tsb_history=[],
        ctl_window=[45.0 + i * 0.9 for i in range(8)],     # +6.3 CTL/semana
    )


def _weekly_tss(hz) -> list[float]:
    return [sum(d.tss for d in hz[w * 7:(w + 1) * 7]) for w in range(len(hz) // 7)]


def test_deload_week_actually_cuts_weekly_load():
    hz = roll_horizon(
        ftp=348.0, ctl=50.0, atl=48.0, context=_loaded_ctx(),
        days=28, start=date(2026, 8, 1),
    )
    weeks = _weekly_tss(hz)
    deload = [
        w for w in range(4)
        if any("descarga" in d.plan.rationale for d in hz[w * 7:(w + 1) * 7])
    ]
    assert deload, "tras 3 semanas construyendo debe aparecer una descarga"
    w = deload[0]
    # La descarga BAJA de verdad la carga semanal (no solo se etiqueta).
    assert weeks[w] < 0.75 * weeks[w - 1]


def test_deload_resets_and_load_resumes():
    # El fallo anterior: la descarga se quedaba pegada para siempre.
    hz = roll_horizon(
        ftp=348.0, ctl=50.0, atl=48.0, context=_loaded_ctx(),
        days=42, start=date(2026, 8, 1),
    )
    flags = [
        any("descarga" in d.plan.rationale for d in hz[w * 7:(w + 1) * 7])
        for w in range(6)
    ]
    assert flags.count(True) >= 1 and flags.count(False) >= 3   # alterna
    weeks = _weekly_tss(hz)
    w = flags.index(True)
    assert weeks[w + 1] > weeks[w]        # tras descargar, se vuelve a construir


def test_no_deload_without_building():
    # Si la carga NO ha subido, no hay nada que descargar.
    flat = TrainingContext(
        recent=[RecentDay(date(2026, 7, 25) + timedelta(days=i), 40.0, 0.6) for i in range(7)],
        fitness_pct=0.5, tsb_history=[], ctl_window=[45.0] * 8,   # CTL plano
    )
    hz = roll_horizon(
        ftp=348.0, ctl=45.0, atl=45.0, context=flat, days=14, start=date(2026, 8, 1),
    )
    assert not any("descarga" in d.plan.rationale for d in hz[:7])
