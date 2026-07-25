"""Métricas de potencia por actividad (funciones puras, sin FTP).

La serie de vatios se asume muestreada a 1 Hz (como entrega Strava). Ninguna de
estas métricas necesita FTP: son la base independiente del atleta desde la que
luego se estiman CP/W' y FTP, y con FTP el IF y el TSS.

Referencias: Normalized Power / Variability Index (Coggan); Mean Maximal Power
(curva potencia-duración) como entrada al modelo de Critical Power (Monod-Scherrer).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

# Duraciones canónicas de la curva de potencia media-máxima (segundos).
DEFAULT_DURATIONS_S: tuple[int, ...] = (5, 15, 30, 60, 300, 600, 1200, 1800, 3600)


def _clean(watts: Sequence[float | None]) -> np.ndarray:
    """Serie de vatios como float; None y negativos -> 0 (huecos/frenadas)."""
    a = np.array([0.0 if w is None else float(w) for w in watts], dtype=float)
    a[a < 0] = 0.0
    return a


def average_power(watts: Sequence[float | None]) -> float | None:
    a = _clean(watts)
    return float(a.mean()) if a.size else None


def normalized_power(watts: Sequence[float | None], sample_hz: float = 1.0) -> float | None:
    """NP: media móvil de 30 s → elevar a la 4ª → media → raíz 4ª.

    Penaliza la variabilidad: refleja mejor el coste fisiológico que la media.
    Devuelve None si la actividad dura menos que la ventana de 30 s.
    """
    a = _clean(watts)
    win = max(1, round(30 * sample_hz))
    if a.size < win:
        return None
    rolling = np.convolve(a, np.ones(win) / win, mode="valid")
    return float(np.mean(rolling**4) ** 0.25)


def variability_index(
    watts: Sequence[float | None], sample_hz: float = 1.0
) -> float | None:
    """VI = NP / potencia media. ~1.0 = esfuerzo constante; alto = intervalos."""
    np_val = normalized_power(watts, sample_hz)
    avg = average_power(watts)
    if not np_val or not avg:
        return None
    return np_val / avg


def work_kj(watts: Sequence[float | None], sample_hz: float = 1.0) -> float:
    """Trabajo mecánico total en kJ (integral de la potencia)."""
    a = _clean(watts)
    return float(a.sum() / sample_hz / 1000.0)


def mean_maximal_power(
    watts: Sequence[float | None],
    durations_s: Sequence[int] = DEFAULT_DURATIONS_S,
    sample_hz: float = 1.0,
) -> dict[int, float]:
    """Curva MMP: mejor potencia media sostenida para cada duración.

    Usa sumas acumuladas para O(n) por duración. Omite duraciones más largas
    que la actividad.
    """
    a = _clean(watts)
    n = a.size
    cumsum = np.concatenate(([0.0], np.cumsum(a)))
    out: dict[int, float] = {}
    for d in durations_s:
        win = int(round(d * sample_hz))
        if 0 < win <= n:
            window_sums = cumsum[win:] - cumsum[:-win]
            out[int(d)] = float(window_sums.max() / win)
    return out
