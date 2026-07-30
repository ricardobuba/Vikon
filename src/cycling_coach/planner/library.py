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
    rest = "rest"                # descanso total (0 carga) — solo lo dispara la sim
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


# Las sesiones acaban en múltiplos de este paso. Un entreno que dura 93 minutos
# es una cifra de hoja de cálculo, no algo que nadie planifique: se sale a rodar
# hora y media. El ajuste se hace ALARGANDO LA VUELTA A LA CALMA, nunca tocando
# los intervalos: la estructura del estímulo es fisiología y no se redondea.
DURATION_STEP = 15


def _round_up(total: float, step: int = DURATION_STEP) -> float:
    """Minutos que faltan para llegar al siguiente múltiplo de `step`."""
    resto = total % step
    return 0.0 if resto == 0 else step - resto


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
    """Construye una plantilla de intervalos: calentamiento + N×min + vuelta.

    La vuelta a la calma se estira lo justo para que el TOTAL caiga en un
    múltiplo de 15 min."""
    work = warmup + reps * (minutes + rest) + cooldown
    cooldown += _round_up(work)
    total = int(warmup + reps * (minutes + rest) + cooldown)
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
            f"{label}: {reps}×{int(minutes)}' a {int(low)}–{int(high)}% FTP "
            f"({total // 60}:{total % 60:02d} en total)."
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
    objective: Objective, label: str, total: float, low: float, high: float,
    *, warmup: float = 0.0, cooldown: float = 0.0,
) -> WorkoutTemplate:
    """Bloque continuo (resistencia / recuperación), con calentamiento opcional.

    `total` es la duración de PUERTA A PUERTA, que es como se piensa el tiempo
    disponible ("tengo dos horas"). El calentamiento y la vuelta salen de dentro,
    no se suman por encima: así una sesión de 2 h dura 2 h, y no 2:20."""
    minutes = total - warmup - cooldown
    if minutes <= 0:
        raise ValueError(f"{label}: {total} min no dan ni para calentar y enfriar")
    blocks: list[Block] = []
    if warmup:
        blocks.append(Block("warmup", warmup, 55, 65))
    blocks.append(Block("steady", minutes, low, high))
    if cooldown:
        blocks.append(Block("cooldown", cooldown, 55, 60))
    total = int(total)
    return WorkoutTemplate(
        id=f"{objective.value}-{total}",
        objective=objective,
        name=f"{label} {total // 60}:{total % 60:02d}",
        blocks=blocks,
        description=f"{label}: {int(minutes)}' continuos a {int(low)}–{int(high)}% FTP.",
    )


_REST = WorkoutTemplate(
    id="rest",
    objective=Objective.rest,
    name="Descanso total",
    blocks=[],                       # sin bloques → 0 min, 0 TSS
    description="Día libre: la recuperación pasiva es hoy el mejor entreno.",
)


LIBRARY: dict[Objective, WorkoutFamily] = {
    Objective.rest: WorkoutFamily(
        Objective.rest, "Descanso",
        "Día libre para recuperar.",
        [_REST],
    ),
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
        # Escalera de 30 en 30 sobre duraciones REDONDAS. En ciclismo el volumen
        # es el estímulo principal, así que conviene que los escalones sean finos
        # y que encajen en el hueco real: con 2 h libres debe caber una de 2 h,
        # no quedarse en 1:50 por un calentamiento sumado por fuera.
        [
            _steady(Objective.endurance, "Resistencia Z2", 60, 65, 75, warmup=10, cooldown=10),
            _steady(Objective.endurance, "Resistencia Z2", 90, 65, 75, warmup=10, cooldown=10),
            _steady(Objective.endurance, "Resistencia Z2", 120, 65, 75, warmup=10, cooldown=10),
            _steady(Objective.endurance, "Resistencia Z2", 150, 65, 75, warmup=10, cooldown=10),
            _steady(Objective.endurance, "Resistencia Z2", 180, 65, 75, warmup=10, cooldown=10),
        ],
    ),
    Objective.sweet_spot: WorkoutFamily(
        Objective.sweet_spot, "Sweet Spot",
        "Estímulo sub-umbral sostenible: mucho beneficio, fatiga contenida.",
        _ladder(
            Objective.sweet_spot, "Sweet Spot", 88, 93,
            warmup=15, cooldown=10, rest=5,
            # El escalón corto existe para que un hueco de 1 h siga teniendo
            # opción de calidad: si el mínimo fuera 1:30, quien entrena entre
            # semana no haría intensidad nunca.
            doses=[(2, 12), (3, 12), (3, 15), (4, 12), (4, 15)],
        ),
    ),
    Objective.threshold: WorkoutFamily(
        Objective.threshold, "Umbral",
        "Eleva el FTP con esfuerzos al umbral.",
        _ladder(
            Objective.threshold, "Umbral", 95, 100,
            warmup=15, cooldown=10, rest=5,
            doses=[(3, 6), (3, 8), (3, 10), (4, 10), (4, 12)],
        ),
    ),
    Objective.vo2max: WorkoutFamily(
        Objective.vo2max, "VO2máx",
        "Máximo estímulo aeróbico; requiere estar fresco.",
        _ladder(
            Objective.vo2max, "VO2máx", 110, 118,
            warmup=20, cooldown=10, rest=4,
            doses=[(4, 3), (4, 4), (5, 4), (6, 4), (5, 5)],
        ),
    ),
}


# Tiradas largas de verdad, FUERA de la escalera normal a propósito.
#
# Si se metieran como variantes de la familia, `select_template` —que elige por
# `fitness_pct * (n-1)`— reescalaría la dosis de TODOS los días: alargar la
# lista subiría el rodaje habitual de 120 a 240 min sin que nadie lo pidiera.
# Aquí solo las alcanza el DÍA LARGO, y únicamente si el tiempo disponible y la
# simulación lo permiten. La duración es el estímulo de ese día, así que es el
# único donde debe escalar con el tiempo real.
LONG_RIDES: list[WorkoutTemplate] = [
    _steady(Objective.endurance, "Fondo largo", 210, 63, 73, warmup=10, cooldown=10),
    _steady(Objective.endurance, "Fondo largo", 240, 62, 72, warmup=10, cooldown=10),
    _steady(Objective.endurance, "Fondo largo", 300, 60, 70, warmup=10, cooldown=10),
    _steady(Objective.endurance, "Fondo largo", 360, 60, 70, warmup=10, cooldown=10),
]


def select_template(
    objective: Objective,
    fitness_pct: float | None = None,
    minutes: float | None = None,
    level_offset: int = 0,
) -> WorkoutTemplate:
    """Elige la variante de dosis de la familia (grieta 4).

    - `fitness_pct` (0–1): percentil del CTL actual del atleta en su historia →
      sobrecarga AUTORREGULADA (más en forma ⇒ más dosis). None ⇒ nivel medio.
    - `level_offset`: desplaza escalones (p. ej. taper = −2: recorta volumen
      manteniendo el tipo de sesión).
    - `minutes`: tope de tiempo disponible hoy → baja de escalón hasta encajar.
    """
    variants = LIBRARY[objective].variants
    n = len(variants)
    level = round((fitness_pct if fitness_pct is not None else 0.5) * (n - 1))
    level = max(0, min(level + level_offset, n - 1))

    if minutes is not None:
        while level > 0 and variants[level].total_minutes() > minutes:
            level -= 1
    return variants[level]
