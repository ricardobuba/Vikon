"""Tests de DFA-α1 con señales de exponente conocido."""

from __future__ import annotations

import numpy as np

from cycling_coach.physiology import dfa_alpha1, intensity_domain_from_alpha1


def test_white_noise_gives_alpha_near_half():
    # Ruido blanco (no correlacionado) → α1 ≈ 0.5.
    rng = np.random.default_rng(42)
    rr = (800.0 + rng.normal(0, 30, 3000)).tolist()
    alpha = dfa_alpha1(rr)
    assert alpha is not None
    assert 0.35 < alpha < 0.65


def test_brownian_gives_alpha_above_one():
    # Paseo aleatorio (integrado, muy correlacionado) → α1 alto (~1.5).
    rng = np.random.default_rng(7)
    rr = (800.0 + np.cumsum(rng.normal(0, 3, 3000))).tolist()
    alpha = dfa_alpha1(rr)
    assert alpha is not None
    assert alpha > 1.2


def test_none_when_too_few_beats():
    assert dfa_alpha1([800.0] * 10) is None


def test_intensity_domain_thresholds():
    assert intensity_domain_from_alpha1(0.95) == "moderado"
    assert intensity_domain_from_alpha1(0.60) == "pesado"
    assert intensity_domain_from_alpha1(0.40) == "severo"
