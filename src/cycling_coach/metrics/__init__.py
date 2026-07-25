"""Métricas derivadas de las actividades (Fase 2 — parte mecanística).

Empezamos por las de potencia, independientes de FTP: NP, VI, trabajo y la
curva de potencia media-máxima (MMP), base para estimar CP/W' y FTP.
"""

from cycling_coach.metrics.power import (
    DEFAULT_DURATIONS_S,
    average_power,
    mean_maximal_power,
    normalized_power,
    variability_index,
    work_kj,
)

__all__ = [
    "DEFAULT_DURATIONS_S",
    "average_power",
    "mean_maximal_power",
    "normalized_power",
    "variability_index",
    "work_kj",
]
