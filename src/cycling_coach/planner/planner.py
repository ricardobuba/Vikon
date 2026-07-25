"""Planificador mínimo (Fase 3): estado → objetivo → sesión → explicación.

Determinista y explicable: la decisión la toma esta lógica (no un LLM). Jerarquía
del cap. 6: 1) objetivo fisiológico según el estado (forma/fatiga), 2) plantilla
de la biblioteca, 3) sesión concreta en vatios. Todo con su porqué.

Es 1 candidato razonable (Fase 3). La búsqueda multi-candidato + simulación +
scoring es Fase 4.
"""

from __future__ import annotations

from dataclasses import dataclass

from cycling_coach.planner.library import LIBRARY, Objective, WorkoutTemplate


@dataclass
class PlannedSession:
    objective: Objective
    template: WorkoutTemplate
    ftp: float
    rationale: str
    targets: list[str]           # bloques renderizados en vatios


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
) -> PlannedSession:
    """Genera la sesión recomendada + explicación a partir del estado."""
    objective, reason = choose_objective(tsb, ctl, atl, cri)
    template = LIBRARY[objective]
    rationale = (
        f"Objetivo: {objective.value} — {reason} "
        f"Sesión: {template.name} ({template.description})"
    )
    return PlannedSession(
        objective=objective,
        template=template,
        ftp=ftp,
        rationale=rationale,
        targets=render_targets(template, ftp),
    )
