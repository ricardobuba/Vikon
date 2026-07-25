"""Durability: cómo cae el CP con la fatiga acumulada (kJ) dentro de una salida.

Tu CP "fresco" (inicio de salida) es mayor que tu CP tras 3000 kJ. Modelarlo:
  1. Por cada salida, se localiza el mejor esfuerzo de una duración dada y se
     mide cuántos kJ se hicieron ANTES de ese esfuerzo (su contexto de fatiga).
  2. Regresando potencia ~ P_fresco − k·kJ_previos sobre muchas salidas se obtiene
     el coeficiente de durability (W perdidos por kJ) y la potencia fresca.

Esto corrige el sesgo del CP actual: si tus esfuerzos recientes fueron con fatiga,
el CP sin corregir sale bajo. Referencias: durability (Maunder/Spragg/van Erp).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cycling_coach.metrics.cleaning import clean_power


def best_effort_with_context(
    watts: list, duration_s: int, sample_hz: float = 1.0
) -> tuple[float, float] | None:
    """Mejor esfuerzo de `duration_s` en la serie y los kJ acumulados ANTES de él.
    Devuelve (potencia_media, kJ_previos) o None si la actividad es más corta."""
    a = np.asarray(clean_power(watts, sample_hz), dtype=float)
    n = a.size
    win = int(round(duration_s * sample_hz))
    if win <= 0 or win > n:
        return None
    cumsum = np.concatenate(([0.0], np.cumsum(a)))
    window_sums = cumsum[win:] - cumsum[:-win]
    best_i = int(np.argmax(window_sums))
    best_power = float(window_sums[best_i] / win)
    kj_before = float(cumsum[best_i] / sample_hz / 1000.0)   # J → kJ
    return best_power, kj_before


@dataclass
class DurabilityFit:
    fresh_power: float     # potencia fresca a la duración usada (kJ_previos = 0)
    k_dur: float           # W perdidos por kJ acumulado (>0 = se fatiga)
    sd_k: float
    r2: float
    n: int


def estimate_durability(
    efforts: list[tuple[float, float]], min_points: int = 8
) -> DurabilityFit | None:
    """Ajusta potencia ~ fresh − k·kJ_previos. `efforts` = [(kJ_previos, potencia)].
    Devuelve None si hay pocos puntos o casi no hay variación de fatiga."""
    if len(efforts) < min_points:
        return None
    kj = np.array([e[0] for e in efforts], dtype=float)
    p = np.array([e[1] for e in efforts], dtype=float)
    if kj.max() - kj.min() < 300.0:      # sin rango de fatiga no se identifica
        return None

    design = np.vstack([-kj, np.ones_like(kj)]).T    # [-kJ, 1] → [k, fresh]
    coef, *_ = np.linalg.lstsq(design, p, rcond=None)
    k_dur, fresh = float(coef[0]), float(coef[1])

    pred = design @ coef
    ss_res = float(np.sum((p - pred) ** 2))
    ss_tot = float(np.sum((p - p.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    dof = max(1, kj.size - 2)
    sigma2 = ss_res / dof
    cov = sigma2 * np.linalg.inv(design.T @ design)
    sd_k = float(np.sqrt(max(cov[0, 0], 0.0)))
    return DurabilityFit(fresh_power=fresh, k_dur=k_dur, sd_k=sd_k, r2=r2, n=kj.size)
