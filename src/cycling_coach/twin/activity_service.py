"""Listado de actividades con métricas derivadas y un resumen en texto.

El resumen lo redacta CÓDIGO DETERMINISTA a partir de los números reales de la
sesión (grey-box: el motor calcula, nada se inventa). No pasa por el LLM: es
instantáneo, gratis y no puede alucinar cifras.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cycling_coach.db.models import Activity
from cycling_coach.db.repositories import latest_parameter_estimate
from cycling_coach.physiology.training_load import training_stress_score

# Cortes de IF (NP/FTP) → cómo de exigente fue la sesión.
_IF_BANDS = [
    (0.60, "recuperación"),
    (0.75, "resistencia (Z2)"),
    (0.85, "tempo / sweet spot"),
    (0.95, "umbral"),
    (1.05, "VO2máx"),
]


def _band(intensity: float) -> str:
    for cut, label in _IF_BANDS:
        if intensity < cut:
            return label
    return "esfuerzo máximo"


@dataclass
class ActivitySummary:
    id: int
    day: date
    name: str | None
    sport: str
    minutes: float
    distance_km: float | None
    elevation_m: float | None
    avg_power_w: float | None
    np_w: float | None
    max_power_w: float | None
    avg_hr: float | None
    kilojoules: float | None
    intensity: float | None      # IF = NP/FTP
    tss: float | None
    text: str                    # resumen en lenguaje natural (determinista)


def _fmt_dur(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h}h {m:02d}min" if h else f"{m} min"


def _summarize(a: Activity, ftp: float | None, minutes: float,
               intensity: float | None, tss: float | None) -> str:
    """Frase corta que explica QUÉ fue la sesión, solo con datos medidos."""
    parts: list[str] = []
    dur = _fmt_dur(minutes)
    if intensity is not None:
        parts.append(f"Sesión de {_band(intensity)} de {dur} (IF {intensity:.2f})")
    else:
        parts.append(f"Sesión de {dur}")
    if a.distance_m:
        km = a.distance_m / 1000
        extra = f" y {a.elevation_gain_m:.0f} m de desnivel" if a.elevation_gain_m else ""
        parts.append(f"{km:.1f} km{extra}")
    if a.weighted_avg_power_w:
        avg = f" (media {a.avg_power_w:.0f} W)" if a.avg_power_w else ""
        parts.append(f"potencia normalizada {a.weighted_avg_power_w:.0f} W{avg}")
    elif a.avg_power_w:
        parts.append(f"potencia media {a.avg_power_w:.0f} W")
    if a.avg_hr:
        parts.append(f"pulso medio {a.avg_hr:.0f} ppm")
    text = ". ".join([parts[0] + (": " + ", ".join(parts[1:]) if len(parts) > 1 else "")])
    if tss:
        text += f". Carga: {tss:.0f} TSS"
        if tss >= 150:
            text += " — día grande, toca cuidar la recuperación"
        elif tss < 40:
            text += " — carga ligera"
    if a.kilojoules:
        text += f". Gasto ≈ {a.kilojoules:.0f} kJ"
    return text + "."


def list_activities(
    session: Session, athlete_id: int, limit: int = 30
) -> list[ActivitySummary]:
    """Últimas `limit` actividades con métricas y resumen. Más recientes primero."""
    ftp = latest_parameter_estimate(session, athlete_id, "ftp")
    rows = session.execute(
        select(Activity)
        .where(Activity.athlete_id == athlete_id)
        .order_by(Activity.start_time.desc())
        .limit(limit)
    ).scalars().all()

    out: list[ActivitySummary] = []
    for a in rows:
        secs = a.moving_time_s or a.elapsed_time_s or 0
        minutes = secs / 60
        intensity = (
            a.weighted_avg_power_w / ftp
            if (ftp and a.weighted_avg_power_w) else None
        )
        tss = (
            training_stress_score(a.weighted_avg_power_w, secs, ftp)
            if (ftp and a.weighted_avg_power_w and secs) else None
        )
        start = a.start_time
        day = start.date() if isinstance(start, datetime) else start
        out.append(
            ActivitySummary(
                id=a.id, day=day, name=a.name, sport=a.sport, minutes=round(minutes, 1),
                distance_km=round(a.distance_m / 1000, 1) if a.distance_m else None,
                elevation_m=round(a.elevation_gain_m) if a.elevation_gain_m else None,
                avg_power_w=round(a.avg_power_w) if a.avg_power_w else None,
                np_w=round(a.weighted_avg_power_w) if a.weighted_avg_power_w else None,
                max_power_w=round(a.max_power_w) if a.max_power_w else None,
                avg_hr=round(a.avg_hr) if a.avg_hr else None,
                kilojoules=round(a.kilojoules) if a.kilojoules else None,
                intensity=round(intensity, 2) if intensity else None,
                tss=round(tss) if tss else None,
                text=_summarize(a, ftp, minutes, intensity, tss),
            )
        )
    return out
