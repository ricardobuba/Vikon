"""Aprendizaje de los hiperparámetros del filtro por máxima verosimilitud predictiva.

En vez de fijar a ojo q_cp, q_wp, la escala de ruido y la asimetría, los
elegimos maximizando la log-verosimilitud predictiva one-step-ahead del harness
(physiology/backtest.py). Es empirical Bayes / ML tipo-II: los datos deciden la
incertidumbre del modelo → CIs calibradas por construcción.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
from scipy.optimize import minimize

from cycling_coach.physiology.backtest import BacktestResult, backtest_one_step
from cycling_coach.physiology.cp_filter import CPFilterConfig, CPObservation


def _config_from_theta(base: CPFilterConfig, theta: np.ndarray) -> CPFilterConfig:
    """theta = [log q_cp, log obs_scale, down_weight].

    q_wp (ruido de W') NO se optimiza aquí: el backtest solo puntúa el CP, así
    que q_wp no está identificado (se aprendería con un backtest de W' aparte).
    """
    return replace(
        base,
        q_cp=float(np.exp(theta[0])),
        obs_noise_scale=float(np.exp(theta[1])),
        down_weight=float(np.clip(theta[2], 1.0, 12.0)),
    )


def learn_hyperparameters(
    observations: list[CPObservation],
    base_config: CPFilterConfig | None = None,
) -> tuple[CPFilterConfig, BacktestResult, BacktestResult] | None:
    """Optimiza q_cp, q_wp, obs_noise_scale y down_weight para maximizar la
    verosimilitud predictiva. Devuelve (config_aprendida, backtest_antes,
    backtest_después) o None si no hay datos suficientes."""
    base = base_config or CPFilterConfig()
    before = backtest_one_step(observations, base)
    if before is None:
        return None

    def neg_loglik(theta: np.ndarray) -> float:
        cfg = _config_from_theta(base, theta)
        res = backtest_one_step(observations, cfg)
        if res is None or not math.isfinite(res.pred_loglik):
            return 1e12
        return -res.pred_loglik

    theta0 = np.array([
        math.log(base.q_cp),
        math.log(max(base.obs_noise_scale, 1e-3)),
        base.down_weight,
    ])
    result = minimize(
        neg_loglik, theta0, method="Nelder-Mead",
        options={"maxiter": 600, "xatol": 1e-3, "fatol": 1e-3},
    )
    learned = _config_from_theta(base, result.x)
    learned = replace(learned, q_wp=_learn_q_wp(observations, learned))
    after = backtest_one_step(observations, learned)
    if after is None:
        return None
    return learned, before, after


def _learn_q_wp(
    observations: list[CPObservation],
    base: CPFilterConfig,
    grid: tuple[float, ...] = (1e4, 3e4, 1e5, 3e5, 1e6),
) -> float:
    """Aprende q_wp (ruido de proceso de W') maximizando la verosimilitud
    predictiva de W' en el backtest (solo observaciones informativas)."""
    best_q, best_ll = base.q_wp, -np.inf
    for q in grid:
        res = backtest_one_step(observations, replace(base, q_wp=q), target="wprime")
        if res is not None and res.pred_loglik > best_ll:
            best_q, best_ll = q, res.pred_loglik
    return best_q
