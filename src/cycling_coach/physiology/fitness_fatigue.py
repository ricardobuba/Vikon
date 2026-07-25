"""Modelo fitness-fatiga de Banister, FITEADO y VALIDADO contra el CP(t) medido.

Perf(t) = p0 + k1·Fitness(t) − k2·Fatiga(t), donde Fitness/Fatiga son respuestas
exponenciales a la carga (TSS) con constantes τ1 (lenta) y τ2 (rápida). A
diferencia del CTL/ATL descriptivo, aquí se AJUSTAN los parámetros para predecir
la señal de rendimiento real (nuestro CP(t)) y se compara con una baseline
(¿bate a "CTL predice CP"?). Si no aporta, se declara.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np


def _daily_array(daily_tss: dict[date, float], start: date, end: date) -> np.ndarray:
    n = (end - start).days + 1
    a = np.zeros(n)
    for day, tss in daily_tss.items():
        i = (day - start).days
        if 0 <= i < n:
            a[i] += tss
    return a


def _impulse_response(tss: np.ndarray, tau: float) -> np.ndarray:
    """Respuesta exponencial acumulada: R[t] = R[t-1]·e^{-1/τ} + TSS[t]."""
    decay = float(np.exp(-1.0 / tau))
    out = np.zeros_like(tss)
    acc = 0.0
    for t in range(tss.size):
        acc = acc * decay + tss[t]
        out[t] = acc
    return out


@dataclass
class FitnessFatigueFit:
    p0: float
    k1: float
    k2: float
    tau1: float
    tau2: float
    r2: float           # R² del modelo fitness-fatiga sobre el CP(t)
    r2_ctl_baseline: float   # R² de la baseline "CP ~ a + b·CTL"
    n: int

    @property
    def beats_baseline(self) -> bool:
        return self.r2 > self.r2_ctl_baseline + 0.02


def _weighted_r2(y: np.ndarray, pred: np.ndarray, w: np.ndarray) -> float:
    ybar = float(np.sum(w * y) / np.sum(w))
    ss_res = float(np.sum(w * (y - pred) ** 2))
    ss_tot = float(np.sum(w * (y - ybar) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0


def fit_fitness_fatigue(
    daily_tss: dict[date, float],
    cp_obs: list[tuple[date, float, float]],
    tau1_grid: tuple[int, ...] = (21, 28, 35, 42, 50, 60),
    tau2_grid: tuple[int, ...] = (5, 7, 10, 14, 21),
) -> FitnessFatigueFit | None:
    """Ajusta el modelo a `cp_obs` = [(fecha, CP, sd)]. Rejilla en τ1/τ2; para cada
    par, p0/k1/k2 por mínimos cuadrados ponderados. Devuelve el mejor + la baseline."""
    if len(cp_obs) < 6 or not daily_tss:
        return None
    start = min(min(daily_tss), min(o[0] for o in cp_obs))
    end = max(max(daily_tss), max(o[0] for o in cp_obs))
    tss = _daily_array(daily_tss, start, end)
    idx = np.array([(o[0] - start).days for o in cp_obs])
    y = np.array([o[1] for o in cp_obs])
    w = np.array([1.0 / max(o[2], 1.0) ** 2 for o in cp_obs])
    sw = np.sqrt(w)

    def wls(design: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        coef, *_ = np.linalg.lstsq(design * sw[:, None], y * sw, rcond=None)
        return coef, design @ coef

    # Baseline: CP ~ a + b·CTL (CTL = respuesta lenta τ1=42).
    ctl = _impulse_response(tss, 42.0)[idx]
    _, pred_ctl = wls(np.column_stack([np.ones_like(ctl), ctl]))
    r2_ctl = _weighted_r2(y, pred_ctl, w)

    best = None
    for tau1 in tau1_grid:
        fit_series = _impulse_response(tss, float(tau1))[idx]
        for tau2 in tau2_grid:
            if tau2 >= tau1:
                continue
            fat_series = _impulse_response(tss, float(tau2))[idx]
            design = np.column_stack([np.ones_like(fit_series), fit_series, -fat_series])
            coef, pred = wls(design)
            r2 = _weighted_r2(y, pred, w)
            if best is None or r2 > best[0]:
                best = (r2, coef, tau1, tau2)

    r2, coef, tau1, tau2 = best
    return FitnessFatigueFit(
        p0=float(coef[0]), k1=float(coef[1]), k2=float(coef[2]),
        tau1=float(tau1), tau2=float(tau2),
        r2=r2, r2_ctl_baseline=r2_ctl, n=len(cp_obs),
    )
