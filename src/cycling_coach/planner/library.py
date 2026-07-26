"""Biblioteca de entrenamientos por bloques (cap. 7).

Los entrenamientos no son etiquetas: son secuencias de BLOQUES parametrizados en
%FTP. Cada objetivo es una FAMILIA con una escalera de dosis (variantes de menos
a más carga). La selección de dosis (grieta 4) la hace el planner según la forma
relativa del atleta y el tiempo disponible — así hay progresión y adaptación, no
una plantilla rígida.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Objective(StrEnum):
    recovery = "recovery"        # recuperación activa
    endurance = "endurance"      # resistencia aeróbica
    sweet_spot = "sweet_spot"    # sweet spot (sub-umbral)
    threshold = "threshold"      # umbral (FTP)
    vo2max = "vo2max"            # VO2 máx


@dataclass
class Block:
    kind: str                    # warmup|steady|interval|cooldown
    minutes: float
    low_pct: float               # %FTP (inferior)
    high_pct: float              # %FTP (superior)
    repeats: int = 1
    rest_min: float = 0.0        # descanso tras cada repetición (intervalos)


@dataclass
class WorkoutTemplate:
    id: str
    objective: Objective
    name: str
    blocks: list[Block]
    description: str
    meta: dict = field(default_factory=dict)

    def total_minutes(self) -> float:
        total = 0.0
        for b in self.blocks:
            total += b.repeats * b.minutes + max(0, b.repeats) * b.rest_min
        return total


@dataclass
class WorkoutFamily:
    """Un objetivo fisiológico con su escalera de dosis (variantes ordenadas de
    menos a más carga). El planner elige la variante (grieta 4)."""
    objective: Objective
    name: str
    description: str
    variants: list[WorkoutTemplate]   # menor → mayor carga

    def __post_init__(self) -> None:
        # Invariante: la escalera está ordenada por tiempo total (dosis creciente).
        self.variants.sort(key=lambda t: t.total_minutes())


def _intervals(
    objective: Objective,
    label: str,
    reps: int,
    minutes: float,
    low: float,
    high: float,
    *,
    warmup: float,
    cooldown: float,
    rest: float,
) -> WorkoutTemplate:
    """Construye una plantilla de intervalos: calentamiento + N×min + vuelta."""
    return WorkoutTemplate(
        id=f"{objective.value}-{reps}x{int(minutes)}",
        objective=objective,
        name=f"{label} {reps}×{int(minutes)}'",
        blocks=[
            Block("warmup", warmup, 55, 75),
            Block("interval", minutes, low, high, repeats=reps, rest_min=rest),
            Block("cooldown", cooldown, 55, 60),
        ],
        description=(
            f"{label}: {reps}×{int(minutes)}' a {int(low)}–{int(high)}% FTP."
        ),
    )


def _ladder(
    objective: Objective, label: str, low: float, high: float,
    *, warmup: float, cooldown: float, rest: float, doses: list[tuple[int, float]],
) -> list[WorkoutTemplate]:
    """Escalera de variantes de intervalos desde una lista de dosis (reps, min)."""
    return [
        _intervals(
            objective, label, reps, minutes, low, high,
            warmup=warmup, cooldown=cooldown, rest=rest,
        )
        for reps, minutes in doses
    ]


def _steady(
    objective: Objective, label: str, minutes: float, low: float, high: float,
    *, warmup: float = 0.0, cooldown: float = 0.0,
) -> WorkoutTemplate:
    """Bloque continuo (resistencia / recuperación), con calentamiento opcional."""
    blocks: list[Block] = []
    if warmup:
        blocks.append(Block("warmup", warmup, 55, 65))
    blocks.append(Block("steady", minutes, low, high))
    if cooldown:
        blocks.append(Block("cooldown", cooldown, 55, 60))
    return WorkoutTemplate(
        id=f"{objective.value}-{int(minutes + warmup + cooldown)}",
        objective=objective,
        name=f"{label} {int(minutes + warmup + cooldown)}'",
        blocks=blocks,
        description=f"{label}: {int(minutes)}' continuos a {int(low)}–{int(high)}% FTP.",
    )


LIBRARY: dict[Objective, WorkoutFamily] = {
    Objective.recovery: WorkoutFamily(
        Objective.recovery, "Recuperación",
        "Rodaje muy suave para favorecer la recuperación.",
        [
            _steady(Objective.recovery, "Recuperación", 30, 50, 60),
            _steady(Objective.recovery, "Recuperación", 45, 50, 60),
        ],
    ),
    Objective.endurance: WorkoutFamily(
        Objective.endurance, "Resistencia Z2",
        "Base aeróbica: construye fitness sin apenas fatiga.",
        [
            _steady(Objective.endurance, "Resistencia Z2", 60, 65, 75, warmup=10, cooldown=10),
            _steady(Objective.endurance, "Resistencia Z2", 90, 65, 75, warmup=10, cooldown=10),
            _steady(Objective.endurance, "Resistencia Z2", 120, 65, 75, warmup=10, cooldown=10),
            _steady(Objective.endurance, "Resistencia Z2", 180, 65, 75, warmup=10, cooldown=10),
        ],
    ),
    Objective.sweet_spot: WorkoutFamily(
        Objective.sweet_spot, "Sweet Spot",
        "Estímulo sub-umbral sostenible: mucho beneficio, fatiga contenida.",
        _ladder(
            Objective.sweet_spot, "Sweet Spot", 88, 93,
            warmup=15, cooldown=10, rest=5,
            doses=[(3, 12), (3, 15), (4, 12), (4, 15)],
        ),
    ),
    Objective.threshold: WorkoutFamily(
        Objective.threshold, "Umbral",
        "Eleva el FTP con esfuerzos al umbral.",
        _ladder(
            Objective.threshold, "Umbral", 95, 100,
            warmup=15, cooldown=10, rest=5,
            doses=[(3, 8), (3, 10), (4, 10), (4, 12)],
        ),
    ),
    Objective.vo2max: WorkoutFamily(
        Objective.vo2max, "VO2máx",
        "Máximo estímulo aeróbico; requiere estar fresco.",
        _ladder(
            Objective.vo2max, "VO2máx", 110, 118,
            warmup=20, cooldown=10, rest=4,
            doses=[(4, 4), (5, 4), (6, 4), (5, 5)],
        ),
    ),
}


def select_template(
    objective: Objective,
    fitness_pct: float | None = None,
    minutes: float | None = None,
) -> WorkoutTemplate:
    """Elige la variante de dosis de la familia (grieta 4).

    - `fitness_pct` (0–1): percentil del CTL actual del atleta en su historia →
      sobrecarga AUTORREGULADA (más en forma ⇒ más dosis). None ⇒ nivel medio.
    - `minutes`: tope de tiempo disponible hoy → baja de escalón hasta encajar.
    """
    variants = LIBRARY[objective].variants
    n = len(variants)
    level = round((fitness_pct if fitness_pct is not None else 0.5) * (n - 1))
    level = max(0, min(level, n - 1))

    if minutes is not None:
        while level > 0 and variants[level].total_minutes() > minutes:
            level -= 1
    return variants[level]
