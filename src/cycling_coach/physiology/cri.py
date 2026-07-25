"""CRI — Cyclist Readiness Index (índice de forma propio, cap. 5).

CRI = 0.35·Rendimiento + 0.25·(1−Fatiga) + 0.15·Recuperación + 0.15·Tendencia
      + 0.10·Cumplimiento,  con cada término normalizado a [0,1].

Es un RESUMEN heurístico para el usuario y una feature del planificador — NO una
entrada al motor fisiológico (evita circularidad, cap. 5.4). Los pesos son
ajustables/aprendibles por usuario (fase futura).

v1: solo se computan los componentes con datos. Recuperación (HRV/sueño) y
Cumplimiento (plan) aún no disponibles → se renormalizan los pesos sobre lo
disponible y se declara la cobertura. Se muestra con esa limitación, no como un
número cerrado.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

DEFAULT_WEIGHTS: dict[str, float] = {
    "performance": 0.35,
    "freshness": 0.25,      # (1 − Fatiga)
    "recovery": 0.15,
    "trend": 0.15,
    "compliance": 0.10,
}


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def norm_performance(current_cp: float, cp_low: float, cp_high: float) -> float:
    """Rendimiento: CP actual dentro del rango histórico [low, high]."""
    if cp_high <= cp_low:
        return 0.5
    return _clip01((current_cp - cp_low) / (cp_high - cp_low))


def norm_freshness(tsb: float, span: float = 25.0) -> float:
    """(1 − Fatiga) desde el TSB: forma positiva → fresco. TSB=+span → 1, −span → 0."""
    return _clip01((tsb + span) / (2.0 * span))


def norm_trend(ctl_now: float, ctl_prev: float, span: float = 40.0) -> float:
    """Tendencia del fitness: pendiente de CTL. Subiendo → >0.5, bajando → <0.5."""
    return _clip01(0.5 + (ctl_now - ctl_prev) / span)


def norm_recovery(
    sleep_hours: float | None = None,
    feel: float | None = None,
    sleep_floor: float = 5.0,
    sleep_target: float = 8.0,
) -> float | None:
    """Recuperación desde AUTO-REPORTE subjetivo (sin wearable): horas de sueño
    y/o sensación (1–10). Señal validada de disposición. None si no hay ninguno."""
    parts: list[float] = []
    if sleep_hours is not None:
        parts.append(_clip01((sleep_hours - sleep_floor) / (sleep_target - sleep_floor)))
    if feel is not None:
        parts.append(_clip01((feel - 1.0) / 9.0))    # escala 1–10
    if not parts:
        return None
    return sum(parts) / len(parts)


@dataclass
class CRIResult:
    cri: float                                   # 0–100
    components: dict[str, float] = field(default_factory=dict)  # disponibles, [0,1]
    missing: list[str] = field(default_factory=list)
    coverage: float = 0.0                        # fracción del peso total cubierta


@dataclass
class CRICalibration:
    weights: dict[str, float]        # pesos aprendidos (renormalizados)
    corr_default: float              # correlación CRI(defaults) vs rendimiento
    corr_learned: float              # correlación CRI(aprendidos) vs rendimiento
    n: int

    @property
    def improved(self) -> bool:
        # Solo "mejora" si logra una correlación POSITIVA y usable (no basta con
        # ser menos mala): evita guardar pesos que sobreajustan ruido.
        return self.corr_learned > 0.15 and self.corr_learned > self.corr_default + 0.02


def _cri_score(samples: list[tuple[dict[str, float], float]], w: dict[str, float]) -> np.ndarray:
    keys = list(w)
    total = sum(w.values()) or 1.0
    return np.array([sum(w[k] * c[k] for k in keys) / total for c, _ in samples])


def calibrate_weights(
    samples: list[tuple[dict[str, float], float]],
    default_weights: dict[str, float] | None = None,
    ridge: float = 0.5,
) -> CRICalibration | None:
    """Aprende los pesos de los componentes maximizando la correlación con el
    rendimiento observado (regresión regularizada hacia los defaults, pesos ≥0).

    `samples` = [(componentes[0,1], rendimiento_observado)]. Devuelve None con
    pocos datos. Compara la correlación con los pesos por defecto vs aprendidos."""
    w0d = default_weights or DEFAULT_WEIGHTS
    if len(samples) < 8:
        return None
    keys = [k for k in w0d if all(k in c for c, _ in samples)]
    if len(keys) < 2:
        return None

    x = np.array([[c[k] for k in keys] for c, _ in samples], dtype=float)
    y = np.array([o for _, o in samples], dtype=float)
    xc = x - x.mean(axis=0)
    yc = y - y.mean()
    w0 = np.array([w0d[k] for k in keys])

    # Ridge hacia los pesos por defecto.
    a = xc.T @ xc + ridge * np.eye(len(keys))
    b = xc.T @ yc + ridge * w0
    w = np.clip(np.linalg.solve(a, b), 0.0, None)
    avail_total = float(sum(w0d[k] for k in keys))
    w = w / w.sum() * avail_total if w.sum() > 0 else w0
    learned = {k: float(wi) for k, wi in zip(keys, w, strict=True)}

    defaults_avail = {k: w0d[k] for k in keys}

    def _corr(weights: dict[str, float]) -> float:
        s = _cri_score(samples, weights)
        if np.std(s) == 0 or np.std(y) == 0:
            return 0.0
        return float(np.corrcoef(s, y)[0, 1])

    return CRICalibration(
        weights=learned,
        corr_default=_corr(defaults_avail),
        corr_learned=_corr(learned),
        n=len(samples),
    )


def compute_cri(
    components: dict[str, float],
    weights: dict[str, float] | None = None,
) -> CRIResult:
    """Combina los componentes DISPONIBLES (cada uno [0,1]) renormalizando sus
    pesos. `components` solo incluye los que se pudieron calcular."""
    w = weights or DEFAULT_WEIGHTS
    available = {k: v for k, v in components.items() if v is not None}
    missing = [k for k in w if k not in available]
    total_w = sum(w[k] for k in available)
    if total_w <= 0:
        return CRIResult(cri=0.0, components={}, missing=list(w), coverage=0.0)
    score = sum(w[k] * available[k] for k in available) / total_w
    return CRIResult(
        cri=100.0 * score,
        components=available,
        missing=missing,
        coverage=total_w,
    )
