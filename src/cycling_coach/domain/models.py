"""Modelo de datos canónico + estructuras del gemelo digital.

Principio de diseño (cap. 13.2): la capa adaptadora normaliza cada proveedor
(Strava, Garmin, Intervals...) a ESTAS estructuras. Del gemelo hacia dentro,
nadie sabe de qué proveedor vino el dato.

`Estimate` y `AthleteState` son literalmente el cap. 3.3 del documento de diseño:
todo parámetro estimado lleva su incertidumbre.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


# --------------------------------------------------------------------------- #
#  Actividades y streams (datos crudos normalizados)
# --------------------------------------------------------------------------- #
class Sport(StrEnum):
    ride = "ride"
    virtual_ride = "virtual_ride"
    run = "run"
    other = "other"


class StreamType(StrEnum):
    """Canales de serie temporal por actividad (nombres neutrales de proveedor)."""

    time = "time"          # segundos desde el inicio
    watts = "watts"
    heartrate = "heartrate"
    cadence = "cadence"
    velocity = "velocity"  # m/s
    altitude = "altitude"  # m
    distance = "distance"  # m acumulados
    temp = "temp"          # °C
    moving = "moving"      # bool: en movimiento
    grade = "grade"        # % pendiente


@dataclass(frozen=True)
class CanonicalActivity:
    """Una actividad normalizada. `raw` conserva el JSON del proveedor para no
    perder información al reprocesar en el futuro."""

    provider: str
    provider_activity_id: str
    start_time: datetime            # SIEMPRE tz-aware, en UTC
    sport: Sport
    name: str | None = None
    elapsed_time_s: int | None = None
    moving_time_s: int | None = None
    distance_m: float | None = None
    elevation_gain_m: float | None = None
    avg_power_w: float | None = None
    weighted_avg_power_w: float | None = None   # ~ NP (Strava: weighted_average_watts)
    max_power_w: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_cadence: float | None = None
    avg_speed_mps: float | None = None
    kilojoules: float | None = None
    device_watts: bool | None = None            # potencia de medidor real vs estimada
    trainer: bool | None = None                 # rodillo/indoor
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalStream:
    stream_type: StreamType
    data: list                       # serie numérica (o de bool para `moving`)

    @property
    def n_samples(self) -> int:
        return len(self.data)


# --------------------------------------------------------------------------- #
#  Métricas diarias (sueño, HRV, FC reposo, CTL/ATL/TSB, peso...)
# --------------------------------------------------------------------------- #
class DailyMetricType(StrEnum):
    sleep_hours = "sleep_hours"
    hrv_rmssd = "hrv_rmssd"
    resting_hr = "resting_hr"
    body_mass_kg = "body_mass_kg"
    readiness = "readiness"
    ctl = "ctl"          # Chronic Training Load (fitness)
    atl = "atl"          # Acute Training Load (fatiga)
    tsb = "tsb"          # Training Stress Balance (forma)


@dataclass(frozen=True)
class CanonicalDailyMetric:
    metric: str          # valor de DailyMetricType (o libre para métricas nuevas)
    day: date
    value: float
    source: str          # "strava" | "import" | "computed" | ...


# --------------------------------------------------------------------------- #
#  Gemelo digital (cap. 3.3) — con incertidumbre de primera clase
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Estimate:
    """Todo parámetro estimado lleva su incertidumbre (principio 6)."""

    mean: float
    sd: float
    ci90: tuple[float, float]
    updated_at: datetime
    source: str          # "prior" | "test" | "learned" | "import"


@dataclass
class AthleteState:
    """Estado del gemelo en un instante `as_of`.

    Fase 1 puebla `static` + `daily`. `slow` (FTP, CP, W'...) y `latent`
    (variables ocultas) se activan en Fase 2+ (estimación bayesiana).
    """

    static: dict                       # permanentes: sexo, altura, experiencia...
    daily: dict[str, float]            # CTL, ATL, TSB, sueño, HRV, FC reposo...
    as_of: datetime
    slow: dict[str, Estimate] = field(default_factory=dict)     # Fase 2+
    latent: dict[str, Estimate] = field(default_factory=dict)   # Fase 3+
