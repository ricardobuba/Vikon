"""Planificador mínimo (Fase 3): estado → objetivo → sesión → explicación.

Determinista y explicable: la decisión la toma esta lógica (no un LLM). Jerarquía
del cap. 6: 1) objetivo fisiológico según el estado (forma/fatiga), 2) plantilla
de la biblioteca, 3) sesión concreta en vatios. Todo con su porqué.

Es 1 candidato razonable (Fase 3). La búsqueda multi-candidato + simulación +
scoring es Fase 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from cycling_coach.planner.library import LIBRARY, Objective, WorkoutTemplate

# --- Grietas 1+2: mirar más allá del snapshot de hoy -------------------------
# Orden de intensidad de los objetivos (para poder "rebajar" con seguridad).
INTENSITY_RANK: dict[Objective, int] = {
    Objective.recovery: 0,
    Objective.endurance: 1,
    Objective.sweet_spot: 2,
    Objective.threshold: 3,
    Objective.vo2max: 4,
}
_BY_RANK: dict[int, Objective] = {v: k for k, v in INTENSITY_RANK.items()}

# Un día "duro" a efectos de espaciado (regla duro/fácil). IF = NP/FTP de la
# sesión completa: un umbral/VO2 real ronda 0.85+; una Z2 se queda en ~0.70.
HARD_INTENSITY = 0.85
HARD_TSS = 150.0        # o un día muy grande en volumen aunque no sea intenso
MAX_HARD_PER_WEEK = 3   # tope de días de calidad en 7 días (guía polarizada)
RAMP_CAP = 8.0          # subida de CTL/semana considerada agresiva
ACWR_CAP = 1.5          # tope agudo:crónico (atl/ctl)


@dataclass
class RecentDay:
    """Un día de la historia reciente (de más viejo a ayer)."""
    day: date
    tss: float
    intensity: float     # IF-equivalente (0 si descanso)

    @property
    def is_hard(self) -> bool:
        return self.intensity >= HARD_INTENSITY or self.tss >= HARD_TSS


@dataclass
class TrainingContext:
    """Contexto temporal para las restricciones de seguridad (grietas 1+2)."""
    ramp_rate: float | None = None       # CTL(hoy) − CTL(hace 7 d)
    acwr: float | None = None            # atl/ctl (agudo:crónico)
    recent: list[RecentDay] = field(default_factory=list)   # viejo→ayer

    def yesterday_hard(self) -> bool:
        return bool(self.recent) and self.recent[-1].is_hard

    def hard_days_last_week(self) -> int:
        return sum(1 for d in self.recent[-7:] if d.is_hard)


@dataclass
class PlannedSession:
    objective: Objective
    template: WorkoutTemplate
    ftp: float
    rationale: str
    targets: list[str]           # bloques renderizados en vatios
    aspired: Objective | None = None   # objetivo antes de rebajar (si se rebajó)


def choose_objective(
    tsb: float | None,
    ctl: float | None = None,
    atl: float | None = None,
    cri: float | None = None,
) -> tuple[Objective, str]:
    """Elige el objetivo fisiológico del día según la forma (TSB) y la
    disposición (CRI). Devuelve (objetivo, motivo explicable)."""
    if tsb is None:
        return Objective.endurance, "sin datos de forma: base aeróbica por defecto."
    if tsb < -25 or (cri is not None and cri < 40):
        return Objective.recovery, (
            f"forma muy baja (TSB {tsb:+.0f}"
            + (f", CRI {cri:.0f}" if cri is not None else "")
            + "): toca recuperar."
        )
    if tsb < -10:
        return Objective.endurance, (
            f"arrastras fatiga (TSB {tsb:+.0f}): construir sin sobrecargar."
        )
    if tsb < 5:
        return Objective.sweet_spot, (
            f"forma neutra (TSB {tsb:+.0f}): estímulo de calidad sostenible."
        )
    if cri is not None and cri >= 70:
        return Objective.vo2max, (
            f"fresco y con buena disposición (TSB {tsb:+.0f}, CRI {cri:.0f}): "
            "empujar el VO2máx."
        )
    return Objective.threshold, (
        f"fresco (TSB {tsb:+.0f}): sesión de umbral para subir el FTP."
    )


def apply_constraints(
    desired: Objective, ctx: TrainingContext
) -> tuple[Objective, str | None]:
    """Rebaja el objetivo aspirado según la historia reciente y la dinámica de
    carga (grietas 1 y 2). Devuelve (objetivo_final, motivo) — motivo None si no
    se rebaja. Cada restricción impone un TECHO de intensidad; gana el más bajo.

    Los cortes son heurísticos con base en la literatura (regla duro/fácil,
    ≤3 días de calidad/semana, ACWR 0.8–1.3, ramp seguro 3–8 CTL/sem), no
    validados con tus resultados. La validación fina es la grieta 3."""
    rank = INTENSITY_RANK[desired]
    end = INTENSITY_RANK[Objective.endurance]
    ss = INTENSITY_RANK[Objective.sweet_spot]
    rec = INTENSITY_RANK[Objective.recovery]

    # (techo, motivo) de cada guarda que se activa.
    guards: list[tuple[int, str]] = []
    if ctx.yesterday_hard():
        guards.append((end, "ayer fue día duro (regla duro/fácil)"))
    hd = ctx.hard_days_last_week()
    if hd >= MAX_HARD_PER_WEEK:
        guards.append((ss, f"ya llevas {hd} días duros esta semana"))
    if ctx.ramp_rate is not None and ctx.ramp_rate > RAMP_CAP:
        guards.append(
            (end, f"la carga sube rápido (+{ctx.ramp_rate:.0f} CTL/sem): consolidar")
        )
    if ctx.acwr is not None and ctx.acwr > ACWR_CAP:
        guards.append((rec, f"fatiga aguda alta (ACWR {ctx.acwr:.1f})"))

    if not guards:
        return desired, None
    ceiling = min(c for c, _ in guards)
    if ceiling >= rank:                      # ninguna guarda muerde
        return desired, None
    binding = "; ".join(m for c, m in guards if c < rank)
    return _BY_RANK[ceiling], f"ajuste por seguridad: {binding}."


def render_targets(template: WorkoutTemplate, ftp: float) -> list[str]:
    """Convierte cada bloque a vatios reales según el FTP."""
    lines: list[str] = []
    for b in template.blocks:
        lo, hi = round(ftp * b.low_pct / 100), round(ftp * b.high_pct / 100)
        if b.repeats > 1:
            rest = f", rec {b.rest_min:.0f}'" if b.rest_min else ""
            reps = f"{b.repeats}×{b.minutes:.0f}'"
            pct = f"({b.low_pct:.0f}–{b.high_pct:.0f}%)"
            lines.append(f"{reps} {lo}–{hi} W {pct}{rest}")
        else:
            lines.append(
                f"{b.minutes:.0f}' {lo}–{hi} W ({b.low_pct:.0f}–{b.high_pct:.0f}%) [{b.kind}]"
            )
    return lines


def plan_session(
    ftp: float,
    tsb: float | None,
    ctl: float | None = None,
    atl: float | None = None,
    cri: float | None = None,
    context: TrainingContext | None = None,
) -> PlannedSession:
    """Genera la sesión recomendada + explicación a partir del estado.

    1) `choose_objective` decide lo que la FORMA de hoy pide (aspiración).
    2) `apply_constraints` lo rebaja si la HISTORIA reciente o la dinámica de
       carga lo desaconsejan (grietas 1+2). Ambas decisiones son explicables."""
    aspired, reason = choose_objective(tsb, ctl, atl, cri)
    objective, adjust = (aspired, None)
    if context is not None:
        objective, adjust = apply_constraints(aspired, context)

    template = LIBRARY[objective]
    rationale = (
        f"Objetivo: {objective.value} — {reason} "
        f"Sesión: {template.name} ({template.description})"
    )
    if adjust:
        rationale += f" [{adjust}]"
    return PlannedSession(
        objective=objective,
        template=template,
        ftp=ftp,
        rationale=rationale,
        targets=render_targets(template, ftp),
        aspired=aspired if objective is not aspired else None,
    )
