"""Tests del CRI v1 (composición con renormalización de pesos)."""

from __future__ import annotations

import numpy as np

from cycling_coach.physiology.cri import (
    calibrate_weights,
    compute_cri,
    norm_freshness,
    norm_performance,
    norm_recovery,
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


def test_calibrate_moves_toward_true_weights():
    # Rendimiento real = 0.7·performance + 0.3·freshness; trend irrelevante.
    rng = np.random.default_rng(0)
    samples = []
    for _ in range(200):
        a, b, c = rng.random(3)
        outcome = 0.7 * a + 0.3 * b + 0.05 * rng.standard_normal()
        samples.append(({"performance": a, "freshness": b, "trend": c}, outcome))
    cal = calibrate_weights(
        samples, {"performance": 0.34, "freshness": 0.33, "trend": 0.33}, ridge=0.01
    )
    assert cal is not None
    assert cal.weights["performance"] > cal.weights["freshness"]
    assert cal.weights["freshness"] > cal.weights["trend"]   # trend al fondo
    assert cal.corr_learned >= cal.corr_default


def test_calibrate_none_with_few_samples():
    assert calibrate_weights([({"performance": 0.5, "freshness": 0.5}, 0.5)]) is None


def test_recovery_from_subjective_inputs():
    assert norm_recovery(sleep_hours=8.0) == 1.0        # sueño pleno
    assert norm_recovery(sleep_hours=5.0) == 0.0        # sueño mínimo
    assert norm_recovery(feel=10.0) == 1.0              # sensación máxima
    assert norm_recovery(feel=1.0) == 0.0
    assert norm_recovery(sleep_hours=8.0, feel=1.0) == 0.5   # media
    assert norm_recovery() is None                      # sin datos → no aporta


def test_cri_works_the_same_shape_with_or_without_recovery():
    # Sin recuperación (caso de la mayoría): CRI válido con 3 componentes.
    base = {"performance": 0.8, "freshness": 0.6, "trend": 0.5,
            "recovery": None, "compliance": None}
    without = compute_cri(base)
    assert without.coverage == 0.75 and 0 < without.cri <= 100
    # Con recuperación (check-in): entra el componente, cobertura mayor.
    withrec = compute_cri({**base, "recovery": 0.9})
    assert withrec.coverage == 0.90
    assert "recovery" not in withrec.missing


def test_full_coverage_with_checkin_and_compliance():
    """Con check-in (recuperación) y plan registrado (cumplimiento) el CRI ya no
    renuncia a nada: cobertura completa y sin componentes ausentes."""
    full = compute_cri({
        "performance": 0.8, "freshness": 0.6, "trend": 0.5,
        "recovery": 0.9, "compliance": 0.7,
    })
    assert abs(full.coverage - 1.0) < 1e-9
    assert full.missing == []
