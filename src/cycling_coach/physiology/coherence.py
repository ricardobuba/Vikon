"""Coherencia y maximalidad del CP (Paso 3 de robustez).

El modelo P(t)=CP+W'/t asume que las curvas usadas son casi-maximales. Dos fallos
callados que este chequeo saca a la luz, y que afectan directamente al CP actual:

1. ENVOLVENTE ROTA: si un esfuerzo REAL reciente supera a lo que el modelo
   predice para esa duración, el modelo INFRAESTIMA → hay que re-anclar (subir
   CP/W'). Es la señal más accionable de "tu CP está obsoleto".
2. MAXIMALIDAD: si todos tus esfuerzos recientes quedan muy por debajo de la
   curva, no puedes CONFIRMAR el CP actual con datos recientes (esfuerzos
   submaximales) → el CP se sostiene en datos viejos y conviene un test.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DurationCheck:
    seconds: int
    actual: float | None        # mejor potencia real reciente a esa duración
    predicted: float            # CP + W'/t
    ratio: float | None         # actual/predicted (>1 = supera al modelo)
    exceeds: bool               # supera al modelo más allá del margen


@dataclass
class CoherenceReport:
    cp: float
    w_prime: float
    checks: list[DurationCheck] = field(default_factory=list)
    violations: list[DurationCheck] = field(default_factory=list)  # baten el modelo
    maximality: float | None = None    # mejor ratio actual/predicho (0–1+): cercanía
    verdict: str = ""

    @property
    def coherent(self) -> bool:
        return not self.violations


def predicted_power(cp: float, w_prime: float, seconds: int) -> float:
    """Modelo de 2 parámetros: P(t) = CP + W'/t."""
    return cp + w_prime / seconds


def assess_coherence(
    cp: float,
    w_prime: float,
    mmp: dict[int, float],
    *,
    margin: float = 0.03,
    long_min_s: int = 300,
    violation_min_s: int = 180,
) -> CoherenceReport:
    """Compara el modelo (CP/W') con la curva MMP real reciente.

    `margin`: holgura relativa antes de declarar que un esfuerzo bate al modelo
    (ruido de medida). `long_min_s`: duraciones ≥ esto cuentan para la maximalidad
    respecto al CP. `violation_min_s`: solo las duraciones ≥ esto pueden marcar
    envolvente rota — por debajo, el 2-param sobre-predice (W' domina) y una
    'violación' no informaría del CP."""
    report = CoherenceReport(cp=cp, w_prime=w_prime)
    for secs in sorted(mmp):
        actual = mmp[secs]
        pred = predicted_power(cp, w_prime, secs)
        ratio = actual / pred if pred > 0 else None
        exceeds = secs >= violation_min_s and actual > pred * (1.0 + margin)
        check = DurationCheck(secs, actual, pred, ratio, exceeds)
        report.checks.append(check)
        if exceeds:
            report.violations.append(check)

    # Maximalidad: cuán cerca del modelo llegan tus esfuerzos LARGOS (los que
    # informan del CP). 1.0 = tocas la curva; <1 = submaximal.
    longs = [c.ratio for c in report.checks if c.seconds >= long_min_s and c.ratio]
    report.maximality = max(longs) if longs else None
    report.verdict = _verdict(report)
    return report


# A partir de esta duración, superar el modelo informa del CP (asíntota); por
# debajo, de W' (capacidad anaeróbica).
_CP_RANGE_S = 600


def _fmt(c: DurationCheck) -> str:
    dur = f"{c.seconds // 60} min" if c.seconds >= 60 else f"{c.seconds}s"
    return f"{dur} ({c.actual:.0f} W, +{(c.ratio - 1) * 100:.0f}% sobre el modelo)"


def _verdict(r: CoherenceReport) -> str:
    cp_viol = [c for c in r.violations if c.seconds >= _CP_RANGE_S]
    wp_viol = [c for c in r.violations if c.seconds < _CP_RANGE_S]
    if cp_viol:
        worst = max(cp_viol, key=lambda c: (c.ratio or 0))
        return (
            f"CP posiblemente INFRAESTIMADO: tu esfuerzo largo de {_fmt(worst)} "
            "supera al modelo en el rango que define el CP. Conviene re-anclar "
            "(marca ese esfuerzo con `cc mark-test`)."
        )
    if wp_viol:
        worst = max(wp_viol, key=lambda c: (c.ratio or 0))
        return (
            f"CP coherente, pero W' posiblemente BAJO: tu esfuerzo corto de "
            f"{_fmt(worst)} supera al modelo en el rango de W' (no del CP). El CP "
            "a 10–30 min ajusta bien; si haces muchos esfuerzos de 3–6 min, "
            "revisa W'."
        )
    if r.maximality is None:
        return "Sin esfuerzos largos recientes: no se puede confirmar el CP con datos nuevos."
    if r.maximality < 0.90:
        return (
            f"CP coherente pero NO confirmado: tus esfuerzos largos recientes se "
            f"quedan al {r.maximality * 100:.0f}% del modelo (submaximales). "
            "Un test daría certeza."
        )
    return (
        f"CP coherente y confirmado: tus esfuerzos recientes tocan el modelo "
        f"(maximalidad {r.maximality * 100:.0f}%), sin superarlo."
    )
