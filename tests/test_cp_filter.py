"""Tests del filtro de Kalman CP/W' (dos etapas) y del constructor de observaciones."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cycling_coach.physiology.cp_filter import (
    CPFilterConfig,
    CPObservation,
    CriticalPowerFilter,
    build_cp_observations,
    run_cp_filter,
)

BASE = datetime(2021, 1, 1, tzinfo=UTC)


def _obs_series(values, sd_cp=8.0, wp=20000.0, sd_wp=2000.0):
    return [
        CPObservation(BASE + timedelta(days=7 * i), cp, wp, sd_cp, sd_wp)
        for i, cp in enumerate(values)
    ]


def test_tracks_fitness_change():
    obs = _obs_series([300.0] * 20 + [350.0] * 20)
    traj = run_cp_filter(obs, config=CPFilterConfig(q_cp=2.0))
    assert abs(traj[19].cp.mean - 300.0) < 10.0     # fase estable
    assert abs(traj[-1].cp.mean - 350.0) < 12.0     # reancla tras el cambio


def test_converges_and_tightens():
    traj = run_cp_filter(_obs_series([340.0] * 25), config=CPFilterConfig(q_cp=1.0))
    assert abs(traj[-1].cp.mean - 340.0) < 5.0
    assert traj[-1].cp.sd < traj[0].cp.sd           # la incertidumbre baja


def test_uncertainty_grows_without_observations():
    filt = CriticalPowerFilter(320.0, 20000.0, sd_cp0=10.0, config=CPFilterConfig(q_cp=2.0))
    sd0 = filt.state(BASE).cp.sd
    filt.predict(180.0)                              # 6 meses sin datos
    sd1 = filt.state(BASE + timedelta(days=180)).cp.sd
    assert sd1 > sd0


def test_noisy_observations_are_smoothed():
    # Observaciones ruidosas alrededor de 330 → el filtro no debe saltar con cada una.
    vals = [330.0, 345.0, 315.0, 335.0, 325.0, 340.0, 330.0, 328.0]
    traj = run_cp_filter(_obs_series(vals, sd_cp=12.0), config=CPFilterConfig(q_cp=0.5))
    assert 320.0 < traj[-1].cp.mean < 340.0


# --------------------------------------------------------------------------- #
#  Etapa 1: construcción de observaciones desde actividades
# --------------------------------------------------------------------------- #
def _flat_watts(power: float, seconds: int = 1200) -> list[float]:
    return [power] * seconds


def test_build_excludes_anomalous_activity():
    acts = [
        (BASE, "a", _flat_watts(340.0)),
        (BASE + timedelta(days=10), "bad", _flat_watts(900.0)),   # potenciómetro roto
        (BASE + timedelta(days=20), "b", _flat_watts(350.0)),
    ]
    obs = build_cp_observations(acts, window_days=60, stride_days=30)
    assert obs, "debería producir al menos una observación"
    # El CP no debe estar contaminado por los 900 W anómalos.
    assert max(o.cp for o in obs) < 400.0


def test_build_returns_empty_without_activities():
    assert build_cp_observations([]) == []
