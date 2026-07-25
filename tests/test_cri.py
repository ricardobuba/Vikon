"""Tests del CRI v1 (composición con renormalización de pesos)."""

from __future__ import annotations

from cycling_coach.physiology.cri import (
    compute_cri,
    norm_freshness,
    norm_performance,
    norm_trend,
)


def test_component_normalizers_range():
    assert norm_performance(350, 300, 400) == 0.5
    assert norm_performance(500, 300, 400) == 1.0     # clip
    assert norm_freshness(0.0) == 0.5                  # TSB 0 → medio
    assert norm_freshness(25.0) == 1.0                 # muy fresco
    assert norm_trend(50, 50) == 0.5                   # CTL plano
    assert norm_trend(70, 50) > 0.5                    # subiendo


def test_cri_renormalizes_over_available():
    # Solo 3 de 5 componentes (falta recovery y compliance).
    comps = {"performance": 1.0, "freshness": 1.0, "trend": 1.0,
             "recovery": None, "compliance": None}
    res = compute_cri(comps)
    assert abs(res.cri - 100.0) < 1e-6                 # todos a 1 → 100
    assert set(res.missing) == {"recovery", "compliance"}
    assert abs(res.coverage - 0.75) < 1e-6             # 0.35+0.25+0.15


def test_cri_weights_applied():
    # performance=1, resto=0 → CRI = peso_perf / cobertura.
    comps = {"performance": 1.0, "freshness": 0.0, "trend": 0.0,
             "recovery": None, "compliance": None}
    res = compute_cri(comps)
    assert abs(res.cri - 100.0 * 0.35 / 0.75) < 1e-6


def test_cri_zero_when_nothing_available():
    res = compute_cri({"performance": None})
    assert res.cri == 0.0
    assert res.coverage == 0.0
