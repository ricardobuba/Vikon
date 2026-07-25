"""Harness de validación del filtro de CP: backtest one-step-ahead + calibración.

Idea: recorremos las observaciones en orden; en cada paso el filtro PREDICE la
siguiente observación usando solo el pasado, y comparamos con lo observado. De
ahí salen:
  - error / sesgo de predicción (¿acierta y sin sesgo?),
  - cobertura de la CI (¿el 90% cubre el 90%? → calibración, principio 6),
  - NIS (innovación estandarizada al cuadrado; ideal media ≈ 1),
  - log-verosimilitud predictiva (el objetivo a MAXIMIZAR para aprender los
    hiperparámetros, paso 1).

Es el instrumento de medida objetivo: sin esto, ajustar el modelo o los
hiperparámetros sería otra vez "a ojo".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from cycling_coach.physiology.cp_filter import (
    CPFilterConfig,
    CPObservation,
    CriticalPowerFilter,
)


@dataclass
class BacktestResult:
    n: int                 # nº de predicciones evaluadas
    mae: float             # error absoluto medio de CP (W)
    bias: float            # sesgo medio (W); >0 = el modelo predice bajo
    rmse: float
    coverage90: float      # fracción dentro del 90% predictivo (ideal 0.90)
    nis_mean: float        # media de innovación estandarizada² (ideal ~1.0)
    pred_loglik: float     # log-verosimilitud predictiva total (más alto = mejor)

    def summary(self) -> str:
        cal = "calibrada" if 0.83 <= self.coverage90 <= 0.97 else "MAL calibrada"
        conf = ""
        if self.coverage90 < 0.83:
            conf = " (sobre-confiada: CI demasiado estrechas)"
        elif self.coverage90 > 0.97:
            conf = " (infra-confiada: CI demasiado anchas)"
        return (
            f"n={self.n}  MAE={self.mae:.1f}W  sesgo={self.bias:+.1f}W  "
            f"RMSE={self.rmse:.1f}W\n"
            f"cobertura90={self.coverage90:.0%} [{cal}{conf}]  "
            f"NIS={self.nis_mean:.2f}  loglik={self.pred_loglik:.1f}"
        )


def backtest_one_step(
    observations: list[CPObservation],
    config: CPFilterConfig | None = None,
    sd_cp0: float = 30.0,
    sd_wp0: float = 6000.0,
    burn_in: int = 2,
    target: str = "cp",
    informative_sd_wp: float = 8000.0,
) -> BacktestResult | None:
    """Backtest one-step-ahead. `target`="cp" (por defecto) o "wprime". Para W'
    solo se puntúan observaciones INFORMATIVAS (sd_wp < informative_sd_wp: las
    ventanas sin esfuerzo corto llevan sd enorme y no informan). None si faltan
    observaciones."""
    cfg = config or CPFilterConfig()
    if len(observations) < burn_in + 2:
        return None
    dim = 0 if target == "cp" else 1

    first = observations[0]
    filt = CriticalPowerFilter(
        cp0=first.cp, wp0=first.w_prime, sd_cp0=sd_cp0, sd_wp0=sd_wp0, config=cfg
    )
    filt._last = first.when

    ys: list[float] = []
    variances: list[float] = []
    for obs in observations[1:]:
        dt = (obs.when - filt._last).total_seconds() / 86400.0
        filt.predict(dt)
        filt._last = obs.when

        obs_val = obs.cp if dim == 0 else obs.w_prime
        obs_sd = (obs.sd_cp * cfg.obs_noise_scale) if dim == 0 else obs.sd_wp
        informative = dim == 0 or obs.sd_wp < informative_sd_wp
        if informative:
            mean_pred = float(filt.x[dim])
            ys.append(obs_val - mean_pred)
            variances.append(float(filt.P[dim, dim]) + obs_sd**2)

        filt.update(obs.cp, obs.w_prime, obs.sd_cp, obs.sd_wp)

    y = np.array(ys[burn_in:])
    s = np.array(variances[burn_in:])
    if y.size == 0:
        return None

    z90 = 1.645
    nis = y**2 / s
    loglik = float(np.sum(-0.5 * np.log(2 * math.pi * s) - 0.5 * nis))
    return BacktestResult(
        n=int(y.size),
        mae=float(np.mean(np.abs(y))),
        bias=float(np.mean(y)),
        rmse=float(np.sqrt(np.mean(y**2))),
        coverage90=float(np.mean(np.abs(y) < z90 * np.sqrt(s))),
        nis_mean=float(np.mean(nis)),
        pred_loglik=loglik,
    )
