"""Limpieza de artefactos en la serie de potencia (previo a estimar CP/W').

Objetivo: quitar basura evidente (picos aislados de 1-2 muestras, valores
absurdos por descalibración) SIN destruir esfuerzos reales. Un sprint sostenido
10 s tiene su mediana local también alta, así que no se marca; un pico aislado
de 1 muestra sí (su ventana es mayormente baja).

Nota: los errores *sostenidos* (un potenciómetro descalibrado toda la salida) no
se corrigen aquí; de eso se encarga el ajuste ROBUSTO de CP (critical_power.py),
que descarta puntos atípicos de la curva.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def _rolling_median(a: np.ndarray, win: int) -> np.ndarray:
    if win < 3:
        win = 3
    if win % 2 == 0:
        win += 1
    if a.size < win:
        return np.full(a.shape, float(np.median(a)) if a.size else 0.0)
    pad = win // 2
    padded = np.pad(a, pad, mode="edge")
    return np.median(sliding_window_view(padded, win), axis=1)


def clean_power(
    watts: Sequence[float | None],
    sample_hz: float = 1.0,
    abs_max_w: float = 2500.0,
    spike_factor: float = 3.0,
    window_s: float = 5.0,
) -> list[float]:
    """Sustituye artefactos por la mediana local y devuelve la serie limpia.

    Un valor es artefacto si supera `abs_max_w` (techo físico) o es más de
    `spike_factor`× la mediana de su ventana (pico aislado).
    """
    a = np.array([0.0 if w is None else float(w) for w in watts], dtype=float)
    a[a < 0] = 0.0
    if a.size == 0:
        return []
    win = max(3, int(round(window_s * sample_hz)))
    med = _rolling_median(a, win)
    is_artifact = (a > abs_max_w) | (a > spike_factor * np.maximum(med, 1.0))
    a[is_artifact] = med[is_artifact]
    return a.tolist()
