"""Tests del modelo fitness-fatiga (Banister) fiteado contra una señal conocida."""

from __future__ import annotations

from datetime import date, timedelta

from cycling_coach.physiology.fitness_fatigue import (
    _impulse_response,
    fit_fitness_fatigue,
)


def test_impulse_response_decays():
    import numpy as np

    tss = np.zeros(100)
    tss[0] = 100.0                      # un solo impulso
    r = _impulse_response(tss, tau=10.0)
    assert r[0] == 100.0
    assert r[10] < r[0]                 # decae
    assert abs(r[10] - 100.0 * np.exp(-1.0)) < 1.0   # ~1 τ → e⁻¹


def test_recovers_and_beats_baseline_on_known_signal():
    import numpy as np

    start = date(2022, 1, 1)
    # Carga con bloques (para que fitness y fatiga difieran).
    daily = {}
    for i in range(500):
        block = (i // 20) % 2 == 0     # 20 días duros, 20 suaves
        daily[start + timedelta(days=i)] = 110.0 if block else 20.0

    tau1, tau2, p0, k1, k2 = 42.0, 7.0, 250.0, 0.05, 0.06
    tss = np.array([daily[start + timedelta(days=i)] for i in range(500)])
    fit_s = _impulse_response(tss, tau1)
    fat_s = _impulse_response(tss, tau2)
    # Observaciones de CP cada 15 días desde el día 100 (modelo + ruido pequeño).
    cp_obs = []
    for i in range(100, 500, 15):
        cp = p0 + k1 * fit_s[i] - k2 * fat_s[i]
        cp_obs.append((start + timedelta(days=i), cp, 3.0))

    fit = fit_fitness_fatigue(daily, cp_obs)
    assert fit is not None
    assert fit.r2 > 0.9                 # recupera la señal
    assert fit.beats_baseline           # la fatiga aporta sobre el CTL solo


def test_none_with_too_few_observations():
    daily = {date(2022, 1, 1): 100.0}
    assert fit_fitness_fatigue(daily, [(date(2022, 1, 1), 300.0, 3.0)]) is None
