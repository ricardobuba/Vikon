"""Tests del simulador de sesión (grieta 6)."""

from __future__ import annotations

from cycling_coach.planner.library import LIBRARY, Objective
from cycling_coach.planner.simulator import (
    choose_dose_by_simulation,
    estimate_session_tss,
    simulate_next_day,
)


def test_tss_estimate_is_ordered_and_sensible():
    # Más intensidad/tiempo ⇒ más TSS; recovery bajo, VO2/umbral altos.
    rec = estimate_session_tss(LIBRARY[Objective.recovery].variants[0])
    thr = estimate_session_tss(LIBRARY[Objective.threshold].variants[-1])
    assert 5 < rec < 30
    assert 80 < thr < 120
    # Dentro de una familia, la escalera de TSS es creciente.
    ss = [estimate_session_tss(v) for v in LIBRARY[Objective.sweet_spot].variants]
    assert ss == sorted(ss)


def test_simulate_next_day_math():
    # Sin carga (TSS=0): CTL y ATL decaen; ATL más rápido ⇒ TSB sube.
    o = simulate_next_day(ctl=50, atl=60, tss=0)
    assert o.ctl_after < 50 and o.atl_after < 60
    assert o.tsb_tomorrow > (50 - 60)         # forma mejora al descansar
    assert o.ctl_gain < 0


def test_hard_session_spikes_fatigue():
    rest = simulate_next_day(50, 50, tss=0)
    hard = simulate_next_day(50, 50, tss=120)
    assert hard.atl_after > rest.atl_after
    assert hard.tsb_tomorrow < rest.tsb_tomorrow   # más fatiga, menos forma
    assert hard.ctl_gain > rest.ctl_gain           # pero más estímulo


def test_choose_dose_picks_biggest_safe_stimulus():
    # Fresco y suelo permisivo → elige la variante de mayor TSS (mayor estímulo).
    c = choose_dose_by_simulation(Objective.threshold, ctl=60, atl=50, tsb_floor=-40)
    assert c.safe
    assert c.template is LIBRARY[Objective.threshold].variants[-1]  # la más dura


def test_choose_dose_rejects_unsafe_and_falls_back_to_softest():
    # Ya hundido + suelo estricto → ninguna variante deja mañana por encima →
    # elige la más suave y lo marca.
    c = choose_dose_by_simulation(Objective.vo2max, ctl=30, atl=70, tsb_floor=0)
    assert not c.safe
    assert c.template is LIBRARY[Objective.vo2max].variants[0]      # la más corta
    assert c.rejected_unsafe == c.considered


def test_time_budget_and_level_cap_limit_candidates():
    # Tope de tiempo: nunca elige algo que no quepa (salvo que ninguna quepa).
    c = choose_dose_by_simulation(
        Objective.sweet_spot, ctl=60, atl=50, tsb_floor=-40, minutes=80
    )
    assert c.template.total_minutes() <= 80
