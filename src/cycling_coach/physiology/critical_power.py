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

from cycling_coach.domain.models import Estimate

# Duraciones (s) usadas para el ajuste (densas dentro del rango CP-válido).
CP_FIT_DURATIONS: tuple[int, ...] = (120, 180, 240, 300, 420, 600, 900, 1200)


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
