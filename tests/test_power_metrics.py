"""Tests de las métricas de potencia (funciones puras, sin BD ni red)."""

from __future__ import annotations

import math

from cycling_coach.metrics.power import (
    average_power,
    mean_maximal_power,
    normalized_power,
    variability_index,
    work_kj,
)


def test_constant_effort_invariants():
    watts = [200.0] * 3600  # 1 h constante a 200 W
    assert average_power(watts) == 200.0
    # NP de un esfuerzo constante ≈ la propia potencia media.
    assert math.isclose(normalized_power(watts), 200.0, rel_tol=1e-9)
    # VI ≈ 1.0 en esfuerzo constante.
    assert math.isclose(variability_index(watts), 1.0, rel_tol=1e-9)
    # Trabajo = 200 W * 3600 s = 720 kJ.
    assert math.isclose(work_kj(watts), 720.0, rel_tol=1e-9)


def test_variable_effort_np_above_average():
    # Alterna 100/300 W: misma media (200) pero NP mayor y VI > 1.
    watts = ([100.0] * 60 + [300.0] * 60) * 30  # 1 h
    assert math.isclose(average_power(watts), 200.0, rel_tol=1e-9)
    assert normalized_power(watts) > 200.0
    assert variability_index(watts) > 1.0


def test_mean_maximal_power_picks_best_window():
    # 100 W, luego un pico de 400 W durante 60 s, luego 100 W.
    watts = [100.0] * 60 + [400.0] * 60 + [100.0] * 60
    mmp = mean_maximal_power(watts, durations_s=[15, 60, 120])
    assert mmp[60] == 400.0        # mejor minuto = el pico
    assert mmp[15] == 400.0        # cabe dentro del pico
    assert 100.0 < mmp[120] < 400.0  # 2 min ya diluye el pico


def test_mean_maximal_power_skips_too_long_durations():
    watts = [200.0] * 100  # 100 s
    mmp = mean_maximal_power(watts, durations_s=[60, 300, 3600])
    assert 60 in mmp
    assert 300 not in mmp   # más largo que la actividad
    assert 3600 not in mmp


def test_none_and_negative_coerced_to_zero():
    watts = [None, -50.0, 100.0, 100.0]
    assert average_power(watts) == 50.0        # (0+0+100+100)/4
    assert math.isclose(work_kj(watts), 0.2)   # 200 W·s = 0.2 kJ


def test_np_none_when_shorter_than_window():
    assert normalized_power([200.0] * 10) is None  # < 30 s
