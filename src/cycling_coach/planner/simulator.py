"""Simulador de sesión (grieta 6): el motor fisiológico como PREDICTOR.

La búsqueda multi-candidato necesita ver las CONSECUENCIAS de cada sesión antes
de elegir. Aquí el modelo fitness-fatiga (mismas τ que CTL/ATL) rueda un día
hacia delante y dice en qué estado te deja mañana. Es física del modelo, no
opinión: el juicio de valor lo ponen las restricciones ya justificadas
(suelo de forma personalizado, taper), no un peso mágico.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from cycling_coach.planner.library import LIBRARY, Objective, WorkoutTemplate

# Intensidad relativa (IF) durante el descanso entre intervalos (rodar suave).
_REST_IF = 0.45


def estimate_session_tss(t: WorkoutTemplate) -> float:
    """TSS previsto de una sesión estructurada a partir de sus bloques en %FTP.

    Cada bloque aporta tiempo·IF²·100 (IF = punto medio del rango). El descanso
    entre repeticiones cuenta como rodaje suave. Es independiente del FTP (todo
    en %FTP), como el propio TSS."""
    tss = 0.0
    for b in t.blocks:
        mid_if = (b.low_pct + b.high_pct) / 200.0        # media / 100
        work_h = b.repeats * b.minutes / 60.0
        tss += work_h * mid_if * mid_if * 100.0
        if b.rest_min and b.repeats:
            rest_h = b.repeats * b.rest_min / 60.0
            tss += rest_h * _REST_IF * _REST_IF * 100.0
    return tss


def session_intensity(t: WorkoutTemplate) -> float:
    """IF representativo de la sesión = el bloque más intenso (los intervalos
    mandan; el calentamiento no cuenta). Sirve para clasificar duro/fácil al
    rodar el horizonte, coherente con RecentDay.is_hard."""
    if not t.blocks:
        return 0.0
    return max((b.low_pct + b.high_pct) / 200.0 for b in t.blocks)


@dataclass
class SimOutcome:
    tss: float
    ctl_after: float
    atl_after: float
    tsb_tomorrow: float      # forma con la que amaneces mañana
    ctl_gain: float          # estímulo de fitness (Δ CTL)


def simulate_next_day(
    ctl: float, atl: float, tss: float, ctl_tau: float = 42.0, atl_tau: float = 7.0
) -> SimOutcome:
    """Rueda CTL/ATL un día con una carga `tss` y devuelve el estado de mañana.

    Solo hace falta el estado de hoy (EWMA es markoviano): no re-simula historia."""
    cd = math.exp(-1.0 / ctl_tau)
    ad = math.exp(-1.0 / atl_tau)
    ctl_after = ctl * cd + tss * (1.0 - cd)
    atl_after = atl * ad + tss * (1.0 - ad)
    return SimOutcome(
        tss=tss,
        ctl_after=ctl_after,
        atl_after=atl_after,
        tsb_tomorrow=ctl_after - atl_after,
        ctl_gain=ctl_after - ctl,
    )


@dataclass
class DoseChoice:
    template: WorkoutTemplate
    outcome: SimOutcome
    safe: bool               # ¿respeta el suelo de forma de mañana?
    considered: int          # nº de variantes evaluadas
    rejected_unsafe: int     # cuántas se descartaron por hundir la forma


def choose_dose_by_simulation(
    objective: Objective,
    ctl: float,
    atl: float,
    tsb_floor: float,
    minutes: float | None = None,
    max_level: int | None = None,
) -> DoseChoice:
    """Elige la variante simulando cada una: el MAYOR estímulo que el modelo
    predice SEGURO (TSB de mañana ≥ tu suelo). Si ninguna es segura, la más
    suave. Filtros previos: tiempo disponible y tope de escalón (taper)."""
    variants = list(enumerate(LIBRARY[objective].variants))
    if minutes is not None:
        fit = [(i, v) for i, v in variants if v.total_minutes() <= minutes]
        variants = fit or variants[:1]
    if max_level is not None:
        capped = [(i, v) for i, v in variants if i <= max_level]
        variants = capped or variants[:1]

    scored = [
        (v, simulate_next_day(ctl, atl, estimate_session_tss(v))) for _, v in variants
    ]
    safe = [(v, o) for v, o in scored if o.tsb_tomorrow >= tsb_floor]
    if safe:
        template, outcome = max(safe, key=lambda s: s[1].tss)
        is_safe = True
    else:
        template, outcome = min(scored, key=lambda s: s[1].tss)
        is_safe = False
    return DoseChoice(
        template=template,
        outcome=outcome,
        safe=is_safe,
        considered=len(scored),
        rejected_unsafe=len(scored) - len(safe),
    )
