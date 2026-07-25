"""Modelo de Critical Power de 2 parámetros (CP, W').

Modelo hiperbólico potencia-duración (Monod-Scherrer / Hill):
    P(t) = CP + W'/t
Reescrito en forma lineal trabajo-tiempo (más estable de ajustar):
    W(t) = P(t)·t = CP·t + W'
→ regresión lineal de W sobre t: pendiente = CP, intercepto = W'.

- **CP**: potencia crítica (W), asíntota sostenible ~ umbral.
- **W'**: capacidad de trabajo anaeróbico por encima de CP (julios).
- **FTP** ≈ `ftp_ratio`·CP (heurística; CP y FTP son cercanos pero no idénticos).

Rango de duraciones válido: ~2–20 min (por debajo domina W'/anaeróbico; por
encima, deriva/agotamiento de glucógeno rompen el modelo de 2 parámetros).

Ajuste ROBUSTO: descarta un `trim` de los peores residuos (p. ej. un punto
contaminado por potencia errónea) y reajusta. La incertidumbre sale de la
covarianza analítica de la regresión (→ `Estimate` con CI del 90%).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
from scipy.optimize import curve_fit

from cycling_coach.domain.models import Estimate

# Duraciones (s) usadas para el ajuste (densas dentro del rango CP-válido).
CP_FIT_DURATIONS: tuple[int, ...] = (120, 180, 240, 300, 420, 600, 900, 1200)

# Duraciones para el modelo de 3 parámetros (incluye cortas para identificar Pmax).
PD3_DURATIONS: tuple[int, ...] = (5, 15, 30, 60, 120, 180, 300, 600, 900, 1200)


@dataclass
class ThreeParamFit:
    cp: Estimate
    w_prime: Estimate
    pmax: Estimate         # potencia máxima instantánea (t→0), W
    r2: float


def three_param_power(t: np.ndarray, cp: float, w_prime: float, pmax: float) -> np.ndarray:
    """Modelo de Morton de 3 parámetros: P(t) = CP + W'/(t + W'/(Pmax−CP)).

    A t→0 tiende a Pmax (finito); a t→∞ tiende a CP. Arregla la potencia infinita
    del modelo de 2 parámetros en duraciones muy cortas.
    """
    return cp + w_prime / (t + w_prime / (pmax - cp))


def fit_3param(
    mmp: dict[int, float], durations: tuple[int, ...] = PD3_DURATIONS
) -> ThreeParamFit:
    """Ajuste no lineal del modelo de 3 parámetros a la curva MMP."""
    ts = sorted(d for d in durations if d in mmp)
    if len(ts) < 4:
        raise ValueError("Se necesitan >=4 duraciones para el modelo de 3 parámetros.")
    t = np.array(ts, dtype=float)
    p = np.array([mmp[d] for d in ts], dtype=float)

    p_short, p_long = float(p.max()), float(p.min())
    p0 = [p_long * 0.95, 20000.0, p_short * 1.15]
    bounds = ([80.0, 1000.0, p_short + 1.0], [500.0, 60000.0, 3000.0])
    popt, pcov = curve_fit(
        three_param_power, t, p, p0=p0, bounds=bounds, maxfev=20000
    )
    perr = np.sqrt(np.diag(pcov))
    pred = three_param_power(t, *popt)
    ss_res = float(np.sum((p - pred) ** 2))
    ss_tot = float(np.sum((p - p.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    now = datetime.now(UTC)
    z90 = 1.645

    def est(mean: float, sd: float) -> Estimate:
        sd = float(sd) if np.isfinite(sd) else 0.0
        return Estimate(mean, sd, (mean - z90 * sd, mean + z90 * sd), now, "import")

    return ThreeParamFit(
        cp=est(float(popt[0]), perr[0]),
        w_prime=est(float(popt[1]), perr[1]),
        pmax=est(float(popt[2]), perr[2]),
        r2=r2,
    )


@dataclass
class CriticalPowerFit:
    cp: Estimate            # potencia crítica (W)
    w_prime: Estimate       # capacidad anaeróbica (J)
    ftp_w: float            # FTP estimado (W)
    durations_used: list[int]
    r2: float


def _linfit_work(t: np.ndarray, work: np.ndarray) -> tuple[float, float, np.ndarray, np.ndarray]:
    design = np.vstack([t, np.ones_like(t)]).T
    coef, *_ = np.linalg.lstsq(design, work, rcond=None)
    cp, w_prime = float(coef[0]), float(coef[1])
    resid = work - design @ coef
    return cp, w_prime, resid, design


def fit_cp_wprime(
    mmp: dict[int, float],
    durations: tuple[int, ...] = CP_FIT_DURATIONS,
    trim: float = 0.25,
    ftp_ratio: float = 0.95,
) -> CriticalPowerFit:
    """Ajusta CP y W' a la curva MMP. `mmp` = {duración_s: vatios}."""
    pts = [(int(d), float(mmp[d])) for d in durations if d in mmp]
    if len(pts) < 3:
        raise ValueError("Se necesitan >=3 duraciones dentro del rango para ajustar CP.")

    t = np.array([d for d, _ in pts], dtype=float)
    work = np.array([w * d for d, w in pts], dtype=float)

    # 1) ajuste inicial; 2) descarta los peores residuos; 3) reajusta.
    _, _, resid, _ = _linfit_work(t, work)
    keep = np.ones(t.size, dtype=bool)
    n_drop = int(np.floor(trim * t.size))
    if n_drop > 0 and t.size - n_drop >= 3:
        keep[np.argsort(np.abs(resid))[-n_drop:]] = False
    cp, w_prime, _, design = _linfit_work(t[keep], work[keep])

    kept_work = work[keep]
    pred = design @ np.array([cp, w_prime])
    ss_res = float(((kept_work - pred) ** 2).sum())
    ss_tot = float(((kept_work - kept_work.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # Incertidumbre: covarianza = σ²·(XᵀX)⁻¹.
    dof = max(1, int(keep.sum()) - 2)
    sigma2 = ss_res / dof
    cov = sigma2 * np.linalg.inv(design.T @ design)
    se_cp = float(np.sqrt(max(cov[0, 0], 0.0)))
    se_wp = float(np.sqrt(max(cov[1, 1], 0.0)))

    now = datetime.now(UTC)
    z90 = 1.645
    cp_est = Estimate(
        mean=cp, sd=se_cp, ci90=(cp - z90 * se_cp, cp + z90 * se_cp),
        updated_at=now, source="import",
    )
    wp_est = Estimate(
        mean=w_prime, sd=se_wp, ci90=(w_prime - z90 * se_wp, w_prime + z90 * se_wp),
        updated_at=now, source="import",
    )
    return CriticalPowerFit(
        cp=cp_est,
        w_prime=wp_est,
        ftp_w=ftp_ratio * cp,
        durations_used=[int(x) for x in t[keep]],
        r2=r2,
    )
