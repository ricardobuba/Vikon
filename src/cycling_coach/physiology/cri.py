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


@dataclass
class CRIResult:
    cri: float                                   # 0–100
    components: dict[str, float] = field(default_factory=dict)  # disponibles, [0,1]
    missing: list[str] = field(default_factory=list)
    coverage: float = 0.0                        # fracción del peso total cubierta


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
