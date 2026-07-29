"""Tests del reconocimiento de sesión (zonas + intervalos, no la media)."""

from __future__ import annotations

from cycling_coach.metrics.session_type import classify, find_intervals

FTP = 300.0


def _ride(*segments: tuple[int, float]) -> list[float]:
    """Serie 1 Hz desde (segundos, vatios)."""
    out: list[float] = []
    for secs, w in segments:
        out.extend([w] * secs)
    return out


def test_vo2_intervals_not_diluted_by_average():
    """El fallo real: 5×5' de VO2 con calentamiento y recuperaciones tenía un IF
    medio bajo y se clasificaba como sweet spot."""
    ride = _ride((20 * 60, 150.0))                       # calentamiento
    for _ in range(5):
        ride += _ride((5 * 60, 345.0), (5 * 60, 120.0))  # 5' a 115% + descanso
    ride += _ride((15 * 60, 140.0))                      # vuelta a la calma
    avg = sum(ride) / len(ride)
    assert avg / FTP < 0.75                              # la media engaña...
    prof = classify(ride, FTP)
    assert prof.kind == "vo2max"                         # ...pero el estímulo no
    assert "5×5'" in prof.detected


def test_threshold_session_detected():
    ride = _ride((15 * 60, 150.0))
    for _ in range(3):
        ride += _ride((12 * 60, 295.0), (5 * 60, 130.0))   # 3×12' al umbral
    prof = classify(ride, FTP)
    assert prof.kind == "threshold"
    assert "3×12'" in prof.detected


def test_long_endurance_ride_is_not_sweet_spot():
    """Una ruta larga acumula minutos en tempo sin ser una sesión de sweet spot."""
    prof = classify(_ride((3 * 3600, 200.0), (30 * 60, 240.0)), FTP)
    assert prof.kind in ("endurance", "tempo")


def test_recovery_ride_stays_recovery():
    assert classify(_ride((45 * 60, 140.0)), FTP).kind in ("recovery", "endurance")


def test_short_spikes_are_not_intervals():
    """Repechos de 20 s no son series: no deben inventar estructura."""
    ride = _ride((30 * 60, 180.0))
    for _ in range(6):
        ride += _ride((20, 330.0), (5 * 60, 170.0))
    assert find_intervals(ride, FTP) == []


def test_dominant_group_survives_extra_block():
    """6×2' + un sprint final se lee como 6×2' (el grupo dominante)."""
    ride = _ride((15 * 60, 150.0))
    for _ in range(6):
        ride += _ride((2 * 60, 375.0), (3 * 60, 120.0))
    ride += _ride((60, 450.0))                            # sprint suelto
    prof = classify(ride, FTP)
    assert prof.kind == "vo2max"
    assert prof.detected.startswith("6×2'")


def test_zone_seconds_add_up():
    # 195 W = 65% FTP → Z2;  290 W = 97% FTP → Z4.
    prof = classify(_ride((600, 195.0), (600, 290.0)), FTP)
    assert prof.zone_seconds["Z2 resistencia"] == 600
    assert prof.zone_seconds["Z4 umbral"] == 600
