"""Tests del mapeo Strava -> canónico (funciones puras, sin BD ni red)."""

from __future__ import annotations

from cycling_coach.adapters.strava.mapper import map_activity, map_streams
from cycling_coach.domain.models import Sport, StreamType

# Recorte representativo de la respuesta de /athlete/activities de Strava.
RAW_ACTIVITY = {
    "id": 123456789,
    "name": "Sweet Spot 3x12",
    "sport_type": "Ride",
    "type": "Ride",
    "start_date": "2024-03-10T07:30:00Z",
    "elapsed_time": 4200,
    "moving_time": 4000,
    "distance": 42000.5,
    "total_elevation_gain": 350.0,
    "average_watts": 210.4,
    "weighted_average_watts": 235,
    "max_watts": 620,
    "average_heartrate": 148.2,
    "max_heartrate": 176.0,
    "average_cadence": 89.1,
    "average_speed": 10.5,
    "kilojoules": 841.0,
    "device_watts": True,
    "trainer": False,
}

RAW_STREAMS = {
    "time": {"data": [0, 1, 2, 3]},
    "watts": {"data": [200, 210, 0, 215]},
    "heartrate": {"data": [140, 142, 145, 148]},
    "moving": {"data": [True, True, False, True]},
    "latlng": {"data": [[41.1, 2.1], [41.1, 2.1]]},  # canal sin mapeo canónico -> se ignora
}


def test_map_activity_fields_and_units():
    act = map_activity(RAW_ACTIVITY)
    assert act.provider == "strava"
    assert act.provider_activity_id == "123456789"      # id como string
    assert act.sport is Sport.ride
    assert act.start_time.tzinfo is not None             # tz-aware
    assert act.start_time.utcoffset().total_seconds() == 0
    assert act.weighted_avg_power_w == 235
    assert act.device_watts is True
    assert act.trainer is False
    assert act.raw["id"] == 123456789                    # se conserva el crudo


def test_map_activity_unknown_sport_falls_back_to_other():
    raw = {**RAW_ACTIVITY, "sport_type": "Kayaking", "type": "Kayaking"}
    assert map_activity(raw).sport is Sport.other


def test_map_activity_handles_missing_optional_fields():
    minimal = {"id": 1, "type": "Ride", "start_date": "2024-01-01T00:00:00Z"}
    act = map_activity(minimal)
    assert act.avg_power_w is None
    assert act.name is None
    assert act.sport is Sport.ride


def test_map_streams_maps_known_channels_and_skips_unknown():
    streams = map_streams(RAW_STREAMS)
    by_type = {s.stream_type: s for s in streams}
    assert StreamType.watts in by_type
    assert StreamType.heartrate in by_type
    assert StreamType.moving in by_type
    assert by_type[StreamType.watts].data == [200, 210, 0, 215]
    assert by_type[StreamType.watts].n_samples == 4
    # `latlng` no tiene tipo canónico y no debe aparecer.
    assert all(s.stream_type != "latlng" for s in streams)


def test_map_streams_skips_empty_channels():
    assert map_streams({"watts": {"data": []}, "heartrate": {}}) == []
