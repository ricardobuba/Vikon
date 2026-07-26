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
from enum import StrEnum

from cycling_coach.planner.library import Objective, WorkoutTemplate, select_template


# --- Grieta 5: meta/evento → fase de temporada (horizonte) -------------------
class Phase(StrEnum):
    off = "off"          # sin meta: planificación reactiva pura
    base = "base"        # >12 sem: construir base aeróbica
    build = "build"      # 6–12 sem: calidad de umbral/sweet spot
    peak = "peak"        # 2–6 sem: intensidad específica (VO2)
    taper = "taper"      # <2 sem: descarga, misma intensidad menos volumen
    race = "race"        # ≤3 días: aperturas / descanso


def phase_for(days_to_event: int | None) -> Phase:
    """Fase de temporada según los días que faltan para el evento objetivo."""
    if days_to_event is None or days_to_event < 0:
        return Phase.off
    if days_to_event <= 3:
        return Phase.race
    if days_to_event <= 14:
        return Phase.taper
    if days_to_event <= 42:
        return Phase.peak
    if days_to_event <= 84:
        return Phase.build
    return Phase.base

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
    """Contexto temporal para las restricciones de seguridad (grietas 1+2) y la
    personalización de umbrales de forma (grieta 3)."""
    ramp_rate: float | None = None       # CTL(hoy) − CTL(hace 7 d)
    acwr: float | None = None            # atl/ctl (agudo:crónico)
    recent: list[RecentDay] = field(default_factory=list)   # viejo→ayer
    tsb_history: list[float] = field(default_factory=list)   # TSB histórico (todo)
    fitness_pct: float | None = None     # percentil del CTL actual (0–1)

    def yesterday_hard(self) -> bool:
        return bool(self.recent) and self.recent[-1].is_hard

    def hard_days_last_week(self) -> int:
        return sum(1 for d in self.recent[-7:] if d.is_hard)


# --- Grieta 3: umbrales de forma personalizados (no mágicos) -----------------
@dataclass
class FormThresholds:
    """Cortes de TSB que separan las zonas de forma. Los defaults son la
    convención poblacional (TrainingPeaks); `personalize` los recentra sobre la
    distribución REAL del atleta — así "fresco" es fresco-PARA-TI, no un número
    universal (arregla el sesgo pro-gran-volumen)."""
    recovery_below: float = -25.0    # TSB < esto → recuperar
    endurance_below: float = -10.0   # TSB < esto → resistencia
    sweet_below: float = 5.0         # TSB < esto → sweet spot; si no, umbral/VO2
    # Por defecto = sweet_below (comportamiento poblacional: VO2 con solo CRI≥70).
    # La personalización lo sube a p88 → zona VO2 genuina (fresco de verdad PARA TI).
    fresh_above: float = 5.0         # TSB ≥ esto (+ CRI alto) → VO2máx

    # nº mínimo de días de TSB para fiarnos de los percentiles.
    MIN_HISTORY = 60

    @classmethod
    def personalize(cls, tsb_history: list[float]) -> FormThresholds:
        """Deriva los cortes de los percentiles p15/p40/p70/p88 del propio
        atleta. Con poca historia devuelve los defaults poblacionales."""
        vals = sorted(v for v in tsb_history if v is not None)
        if len(vals) < cls.MIN_HISTORY:
            return cls()
        p15, p40, p70, p88 = (_percentile(vals, q) for q in (15, 40, 70, 88))
        return cls(
            recovery_below=p15,
            endurance_below=p40,
            sweet_below=p70,
            fresh_above=p88,
        )


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Percentil `q` (0–100) con interpolación lineal. `sorted_vals` ordenada."""
    if not sorted_vals:
        raise ValueError("lista vacía")
    k = (len(sorted_vals) - 1) * q / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


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
    thresholds: FormThresholds | None = None,
) -> tuple[Objective, str]:
    """Elige el objetivo fisiológico del día según la forma (TSB) y la
    disposición (CRI). Devuelve (objetivo, motivo explicable).

    `thresholds` permite recentrar las zonas sobre la distribución del atleta
    (grieta 3); si es None usa la convención poblacional."""
    t = thresholds or FormThresholds()
    if tsb is None:
        return Objective.endurance, "sin datos de forma: base aeróbica por defecto."
    if tsb < t.recovery_below or (cri is not None and cri < 40):
        return Objective.recovery, (
            f"forma muy baja (TSB {tsb:+.0f}"
            + (f", CRI {cri:.0f}" if cri is not None else "")
            + "): toca recuperar."
        )
    if tsb < t.endurance_below:
        return Objective.endurance, (
            f"arrastras fatiga (TSB {tsb:+.0f}): construir sin sobrecargar."
        )
    if tsb < t.sweet_below:
        return Objective.sweet_spot, (
            f"forma neutra (TSB {tsb:+.0f}): estímulo de calidad sostenible."
        )
    if cri is not None and cri >= 70 and tsb >= t.fresh_above:
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


def apply_phase(desired: Objective, phase: Phase) -> tuple[Objective, str | None]:
    """Sesga el objetivo según la fase de temporada (grieta 5). Devuelve
    (objetivo, motivo).

    DECISIÓN DE DISEÑO (confianza): solo actuamos donde la evidencia es fuerte y
    errar es conservador → la SEMANA DE CARRERA baja a aperturas/descanso. NO
    imponemos techos en base/build (prohibir intensidad lejos del evento es UNA
    filosofía de periodización —bloques— que contradice a otras —polarizada— y
    sobrescribiría lo que tu forma pide sin datos que lo respalden). Ese "¿qué
    énfasis toca?" es trabajo del simulador (grieta 6), no de un umbral fijo."""
    if phase is Phase.race:
        ceil = INTENSITY_RANK[Objective.recovery]
        if ceil < INTENSITY_RANK[desired]:
            return _BY_RANK[ceil], "semana de carrera: descarga y aperturas"
    return desired, None


def phase_level_offset(phase: Phase) -> int:
    """Escalones de dosis a recortar por fase. El TAPER (evidencia fuerte:
    recortar volumen manteniendo intensidad afila la forma) y la carrera bajan
    varios escalones. El resto no toca la dosis."""
    return {Phase.taper: -2, Phase.race: -3}.get(phase, 0)


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
    minutes: float | None = None,
    phase: Phase = Phase.off,
    days_to_event: int | None = None,
) -> PlannedSession:
    """Genera la sesión recomendada + explicación a partir del estado.

    1) `choose_objective` decide lo que la FORMA de hoy pide (aspiración), con
       umbrales personalizados (grieta 3).
    2) `apply_constraints` lo rebaja si la HISTORIA reciente o la dinámica de
       carga lo desaconsejan (grietas 1+2).
    3) `apply_phase` sesga por la fase de temporada — solo donde hay evidencia
       fuerte (grieta 5); el resto solo informa del horizonte.
    4) `select_template` elige la DOSIS según la forma relativa (percentil de
       CTL), el tiempo disponible y la descarga de taper/carrera (grietas 4+5).
    Todas las decisiones son explicables."""
    thresholds = None
    fitness_pct = None
    if context is not None:
        if context.tsb_history:
            thresholds = FormThresholds.personalize(context.tsb_history)
        fitness_pct = context.fitness_pct

    aspired, reason = choose_objective(tsb, ctl, atl, cri, thresholds)
    objective, adjust = (aspired, None)
    if context is not None:
        objective, adjust = apply_constraints(aspired, context)
    objective, phase_note = apply_phase(objective, phase)

    template = select_template(
        objective, fitness_pct, minutes, level_offset=phase_level_offset(phase)
    )

    rationale = ""
    if phase is not Phase.off:
        wk = f" (~{days_to_event // 7} sem)" if days_to_event is not None else ""
        rationale += f"[meta en {days_to_event} d{wk} — fase {phase.value}] "
    rationale += (
        f"Objetivo: {objective.value} — {reason} "
        f"Sesión: {template.name} ({template.description})"
    )
    if adjust:
        rationale += f" [{adjust}]"
    if phase_note:
        rationale += f" [{phase_note}]"
    if minutes is not None and template.total_minutes() > minutes:
        rationale += (
            f" [nota: la sesión más corta de calidad ({template.total_minutes():.0f}') "
            f"excede tus {minutes:.0f}' — considera partirla o bajar el objetivo]"
        )
    return PlannedSession(
        objective=objective,
        template=template,
        ftp=ftp,
        rationale=rationale,
        targets=render_targets(template, ftp),
        aspired=aspired if objective is not aspired else None,
    )
