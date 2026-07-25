"""Carga de entrenamiento (TSS) y series de forma CTL/ATL/TSB.

- **TSS** (Training Stress Score): carga de una sesión relativa al FTP.
    TSS = duración_h · IF² · 100,  con IF = NP/FTP.
- **CTL** (fitness): media móvil exponencial del TSS con τ≈42 d.
- **ATL** (fatiga): idem con τ≈7 d.
- **TSB** (forma) = CTL − ATL (del día anterior).

NOTA (debilidad conocida, ver hilo): CTL/ATL/TSB son DESCRIPTIVOS — TSS suavizado,
constantes fijas, no validados. El modelo fitness-fatiga fiteado (paso 3) es el
que se valida contra el CP(t) medido.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta


def training_stress_score(np_w: float, duration_s: float, ftp_w: float) -> float:
    """TSS = (t · NP · IF) / (FTP · 3600) · 100, con IF = NP/FTP."""
    if ftp_w <= 0 or np_w <= 0 or duration_s <= 0:
        return 0.0
    intensity = np_w / ftp_w
    return duration_s * intensity * intensity / 3600.0 * 100.0


@dataclass
class LoadPoint:
    day: date
    ctl: float      # fitness
    atl: float      # fatiga
    tsb: float      # forma (del día anterior)


def compute_ctl_atl_tsb(
    daily_tss: dict[date, float],
    ctl_tau: float = 42.0,
    atl_tau: float = 7.0,
    ctl0: float = 0.0,
    atl0: float = 0.0,
) -> list[LoadPoint]:
    """Serie diaria de CTL/ATL/TSB desde el primer día con carga hasta el último.

    Rellena los días de descanso con TSS=0 (imprescindible para la media móvil).
    Actualización EWMA (Coggan): X_t = X_{t-1}·decay + TSS_t·(1−decay).
    """
    if not daily_tss:
        return []
    ctl_decay = math.exp(-1.0 / ctl_tau)
    atl_decay = math.exp(-1.0 / atl_tau)

    start, end = min(daily_tss), max(daily_tss)
    ctl, atl = ctl0, atl0
    series: list[LoadPoint] = []
    day = start
    while day <= end:
        tsb = ctl - atl                       # forma ANTES del entreno de hoy
        tss = daily_tss.get(day, 0.0)
        ctl = ctl * ctl_decay + tss * (1.0 - ctl_decay)
        atl = atl * atl_decay + tss * (1.0 - atl_decay)
        series.append(LoadPoint(day=day, ctl=ctl, atl=atl, tsb=tsb))
        day += timedelta(days=1)
    return series
