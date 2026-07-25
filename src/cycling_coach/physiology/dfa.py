"""DFA-α1 (Detrended Fluctuation Analysis, exponente α1) sobre intervalos RR.

Marcador NO invasivo del estado autonómico y de los umbrales: α1 cae de ~1.0
(esfuerzo suave, correlaciones fuertes) hacia ~0.5 (esfuerzo severo, señal casi
aleatoria). Cruces típicos: α1≈0.75 ≈ umbral aeróbico (VT1); α1≈0.5 ≈ umbral
anaeróbico (VT2).

⚠️ REQUIERE INTERVALOS RR (ms latido-a-latido), NO el pulso a 1 Hz. Strava NO
expone RR → este algoritmo queda LISTO pero no se puede aplicar hasta ingestar
RR de ficheros .FIT / Garmin / apps de HRV (extensión de Fase 1). No se debe
aproximar con HR a 1 Hz: destruye la información latido-a-latido.
"""

from __future__ import annotations

import numpy as np


def dfa_alpha1(
    rr_ms: list[float], scale_min: int = 4, scale_max: int = 16
) -> float | None:
    """Exponente α1 de DFA sobre la serie de intervalos RR (ventanas 4–16 latidos).

    Devuelve None si hay pocos latidos. Pasos: perfil integrado → detrend lineal
    por ventana → fluctuación RMS F(n) → α1 = pendiente de log F(n) vs log n.
    """
    x = np.asarray(rr_ms, dtype=float)
    x = x[np.isfinite(x)]
    n_beats = x.size
    if n_beats < scale_max * 2:
        return None

    profile = np.cumsum(x - x.mean())          # perfil integrado
    scales = range(scale_min, scale_max + 1)
    log_n: list[float] = []
    log_f: list[float] = []
    for n in scales:
        n_boxes = n_beats // n
        if n_boxes < 1:
            continue
        t = np.arange(n)
        rms_sq = []
        for b in range(n_boxes):
            seg = profile[b * n : (b + 1) * n]
            coef = np.polyfit(t, seg, 1)         # tendencia lineal
            resid = seg - np.polyval(coef, t)
            rms_sq.append(float(np.mean(resid**2)))
        fluct = float(np.sqrt(np.mean(rms_sq)))
        if fluct > 0:
            log_n.append(np.log(n))
            log_f.append(np.log(fluct))
    if len(log_n) < 2:
        return None
    return float(np.polyfit(log_n, log_f, 1)[0])


def intensity_domain_from_alpha1(alpha1: float) -> str:
    """Dominio de intensidad aproximado a partir de α1 (Rogers et al.)."""
    if alpha1 >= 0.75:
        return "moderado"      # por debajo del umbral aeróbico
    if alpha1 >= 0.5:
        return "pesado"        # entre umbral aeróbico y anaeróbico
    return "severo"            # por encima del umbral anaeróbico
