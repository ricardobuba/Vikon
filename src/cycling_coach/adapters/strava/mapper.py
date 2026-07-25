"""Normalización de la representación de Strava al modelo canónico.

Funciones puras y sin efectos: entran dicts crudos de la API, salen dataclasses
canónicas. Aquí es donde se aíslan las particularidades de Strava (unidades,
nombres de campos, tipos de deporte).
"""

from __future__ import annotations

from datetime import UTC, datetime

from cycling_coach.domain.models import (
    CanonicalActivity,
    CanonicalStream,
    Sport,
    StreamType,
)

# Strava usa `sport_type` (nuevo) y `type` (antiguo). Mapeamos ambos.
_SPORT_MAP: dict[str, Sport] = {
    "Ride": Sport.ride,
    "GravelRide": Sport.ride,
    "MountainBikeRide": Sport.ride,
    "EBikeRide": Sport.ride,
    "VirtualRide": Sport.virtual_ride,
    "Run": Sport.run,
    "VirtualRun": Sport.run,
    "TrailRun": Sport.run,
}

# Nombres de canal de Strava -> tipos canónicos.
_STREAM_MAP: dict[str, StreamType] = {
    "time": StreamType.time,
    "watts": StreamType.watts,
    "heartrate": StreamType.heartrate,
    "cadence": StreamType.cadence,
    "velocity_smooth": StreamType.velocity,
    "altitude": StreamType.altitude,
    "distance": StreamType.distance,
    "temp": StreamType.temp,
    "moving": StreamType.moving,
    "grade_smooth": StreamType.grade,
}


def _parse_start(raw: dict) -> datetime:
    """`start_date` viene en ISO-8601 UTC (con sufijo Z)."""
    value = raw["start_date"].replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    return dt.astimezone(UTC)


def map_activity(raw: dict) -> CanonicalActivity:
    sport_key = raw.get("sport_type") or raw.get("type") or ""
    return CanonicalActivity(
        provider="strava",
        provider_activity_id=str(raw["id"]),
        start_time=_parse_start(raw),
        sport=_SPORT_MAP.get(sport_key, Sport.other),
        name=raw.get("name"),
        elapsed_time_s=raw.get("elapsed_time"),
        moving_time_s=raw.get("moving_time"),
        distance_m=raw.get("distance"),
        elevation_gain_m=raw.get("total_elevation_gain"),
        avg_power_w=raw.get("average_watts"),
        weighted_avg_power_w=raw.get("weighted_average_watts"),
        max_power_w=raw.get("max_watts"),
        avg_hr=raw.get("average_heartrate"),
        max_hr=raw.get("max_heartrate"),
        avg_cadence=raw.get("average_cadence"),
        avg_speed_mps=raw.get("average_speed"),
        kilojoules=raw.get("kilojoules"),
        device_watts=raw.get("device_watts"),
        trainer=raw.get("trainer"),
        raw=raw,
    )


def map_athlete(raw: dict) -> dict:
    """Perfil estático desde Strava. Solo expone nombre, sexo y peso;
    fecha de nacimiento y altura NO están en la API (se rellenan en la app).

    Devuelve solo las claves con valor, para poder usarlo como semilla sin
    pisar con `None` datos que el usuario haya editado a mano."""
    first = (raw.get("firstname") or "").strip()
    last = (raw.get("lastname") or "").strip()
    name = " ".join(p for p in (first, last) if p) or None

    profile: dict = {}
    if name:
        profile["name"] = name
    if raw.get("sex") in ("M", "F"):
        profile["sex"] = raw["sex"]
    if raw.get("weight"):  # kg (float)
        profile["weight_kg"] = float(raw["weight"])
    return profile


def map_streams(raw_streams: dict) -> list[CanonicalStream]:
    """`raw_streams` es la respuesta con `key_by_type=true`:
    {"watts": {"data": [...]}, "heartrate": {"data": [...]}, ...}."""
    result: list[CanonicalStream] = []
    for key, canonical in _STREAM_MAP.items():
        channel = raw_streams.get(key)
        if not channel:
            continue
        data = channel.get("data")
        if not data:
            continue
        result.append(CanonicalStream(stream_type=canonical, data=data))
    return result
