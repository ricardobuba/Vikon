"""Tests de limpieza de potencia y del modelo Critical Power."""

from __future__ import annotations

import math

import pytest

from cycling_coach.metrics.cleaning import clean_power
from cycling_coach.physiology import fit_cp_wprime


# --------------------------------------------------------------------------- #
#  Limpieza
# --------------------------------------------------------------------------- #
def test_clean_removes_isolated_spike():
    watts = [200.0] * 20
    watts[10] = 3000.0  # pico aislado de 1 muestra
    cleaned = clean_power(watts)
    assert max(cleaned) < 500.0          # el pico desaparece
    assert cleaned[9] == 200.0           # el resto intacto


def test_clean_preserves_real_sprint():
    # Sprint real sostenido 10 s a 1400 W: NO debe tocarse.
    watts = [200.0] * 10 + [1400.0] * 10 + [200.0] * 10
    cleaned = clean_power(watts)
    assert max(cleaned) == 1400.0


def test_clean_caps_absurd_values():
    watts = [300.0] * 20
    watts[5] = 9000.0
    assert max(clean_power(watts, abs_max_w=2500.0)) <= 2500.0


# --------------------------------------------------------------------------- #
#  Critical Power
# --------------------------------------------------------------------------- #
def _mmp_from(cp: float, w_prime: float, durations) -> dict[int, float]:
    """Curva MMP teórica: P(t) = CP + W'/t."""
    return {int(d): cp + w_prime / d for d in durations}


def test_recovers_known_cp_and_wprime():
    durations = [120, 180, 240, 300, 420, 600, 900, 1200]
    mmp = _mmp_from(cp=300.0, w_prime=20000.0, durations=durations)
    fit = fit_cp_wprime(mmp, trim=0.0)
    assert math.isclose(fit.cp.mean, 300.0, rel_tol=1e-6)
    assert math.isclose(fit.w_prime.mean, 20000.0, rel_tol=1e-6)
    assert fit.r2 > 0.999
    assert math.isclose(fit.ftp_w, 0.95 * 300.0, rel_tol=1e-6)


def test_robust_fit_rejects_outlier():
    durations = [120, 180, 240, 300, 420, 600, 900, 1200]
    mmp = _mmp_from(cp=300.0, w_prime=20000.0, durations=durations)
    mmp[300] = 780.0  # punto contaminado (potencia imposible a 5 min)
    fit = fit_cp_wprime(mmp, trim=0.25)
    # Sin robustez el CP se dispararía; con trim debe seguir cerca de 300.
    assert 285.0 < fit.cp.mean < 315.0
    assert 300 not in fit.durations_used   # el outlier fue descartado


def test_uncertainty_is_reported():
    durations = [120, 240, 300, 600, 1200]
    # Añadimos ruido leve para que la SD no sea cero.
    mmp = _mmp_from(cp=290.0, w_prime=21000.0, durations=durations)
    mmp[300] += 8.0
    fit = fit_cp_wprime(mmp, trim=0.0)
    assert fit.cp.sd > 0.0
    lo, hi = fit.cp.ci90
    assert lo < fit.cp.mean < hi


def test_too_few_points_raises():
    with pytest.raises(ValueError):
        fit_cp_wprime({120: 400.0, 300: 350.0}, durations=(120, 300))
