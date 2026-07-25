"""Biblioteca semilla de entrenamientos por bloques (cap. 7).

Los entrenamientos no son etiquetas: son secuencias de BLOQUES parametrizados en
%FTP. La biblioteca cubre cada objetivo fisiológico con 1 plantilla (Fase 3
mínima; la generación paramétrica de variantes es Fase 4).
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


LIBRARY: dict[Objective, WorkoutTemplate] = {
    Objective.recovery: WorkoutTemplate(
        id="rec-45",
        objective=Objective.recovery,
        name="Recuperación 45'",
        blocks=[Block("steady", 45, 50, 60)],
        description="Rodaje muy suave para favorecer la recuperación.",
    ),
    Objective.endurance: WorkoutTemplate(
        id="end-100",
        objective=Objective.endurance,
        name="Resistencia Z2 100'",
        blocks=[
            Block("warmup", 10, 55, 65),
            Block("steady", 80, 65, 75),
            Block("cooldown", 10, 55, 60),
        ],
        description="Base aeróbica: construye fitness sin apenas fatiga.",
    ),
    Objective.sweet_spot: WorkoutTemplate(
        id="ss-3x12",
        objective=Objective.sweet_spot,
        name="Sweet Spot 3×12'",
        blocks=[
            Block("warmup", 15, 55, 70),
            Block("interval", 12, 88, 93, repeats=3, rest_min=5),
            Block("cooldown", 10, 55, 60),
        ],
        description="Estímulo sub-umbral sostenible: mucho beneficio, fatiga contenida.",
    ),
    Objective.threshold: WorkoutTemplate(
        id="thr-3x10",
        objective=Objective.threshold,
        name="Umbral 3×10'",
        blocks=[
            Block("warmup", 15, 55, 75),
            Block("interval", 10, 95, 100, repeats=3, rest_min=5),
            Block("cooldown", 10, 55, 60),
        ],
        description="Eleva el FTP con esfuerzos al umbral.",
    ),
    Objective.vo2max: WorkoutTemplate(
        id="vo2-5x4",
        objective=Objective.vo2max,
        name="VO2máx 5×4'",
        blocks=[
            Block("warmup", 20, 55, 80),
            Block("interval", 4, 110, 118, repeats=5, rest_min=4),
            Block("cooldown", 10, 55, 60),
        ],
        description="Máximo estímulo aeróbico; requiere estar fresco.",
    ),
}
