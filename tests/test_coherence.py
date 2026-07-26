"""Tests de coherencia/maximalidad del CP (Paso 3 de robustez)."""

from __future__ import annotations

from cycling_coach.physiology.coherence import assess_coherence, predicted_power

CP, WP = 350.0, 20000.0


def _curve(cp=CP, wp=WP, scale=1.0, durations=(180, 300, 600, 1200, 1800)):
    """MMP sintética = modelo · scale (scale<1 submaximal, >1 lo supera)."""
    return {d: predicted_power(cp, wp, d) * scale for d in durations}


def test_confirmed_when_efforts_touch_the_curve():
    r = assess_coherence(CP, WP, _curve(scale=1.0))
    assert r.coherent
    assert r.maximality is not None and r.maximality >= 0.99
    assert "confirmado" in r.verdict


def test_submaximal_efforts_not_confirmed():
    r = assess_coherence(CP, WP, _curve(scale=0.85))
    assert r.coherent                       # no superan el modelo
    assert r.maximality < 0.90
    assert "no confirmado" in r.verdict.lower() or "submaximal" in r.verdict.lower()


def test_cp_underestimated_when_long_effort_beats_model():
    # Un esfuerzo largo (20 min) supera el modelo → CP infraestimado.
    mmp = {1200: predicted_power(CP, WP, 1200) * 1.08}
    r = assess_coherence(CP, WP, mmp)
    assert r.violations
    assert "INFRAESTIMADO" in r.verdict


def test_short_violation_points_to_wprime_not_cp():
    # Solo un esfuerzo corto (5 min) supera → W' bajo, CP coherente.
    mmp = {300: predicted_power(CP, WP, 300) * 1.06,
           1200: predicted_power(CP, WP, 1200) * 1.0}
    r = assess_coherence(CP, WP, mmp)
    assert r.violations
    assert "W'" in r.verdict and "INFRAESTIMADO" not in r.verdict


def test_very_short_efforts_never_flagged():
    # 30s por debajo de violation_min_s: el 2-param sobre-predice, no se marca.
    mmp = {30: predicted_power(CP, WP, 30) * 2.0}   # absurdo, pero corto
    r = assess_coherence(CP, WP, mmp)
    assert not r.violations
