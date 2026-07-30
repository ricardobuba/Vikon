"""MMP persistida: la curva guardada debe ser EXACTAMENTE la que se calculaba.

El riesgo de esta optimización no es que vaya lenta: es que cambie los números
sin que nadie lo note. Por eso los tests comparan contra el cálculo directo, no
contra valores escritos a mano.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cycling_coach.metrics import mean_maximal_power
from cycling_coach.metrics.cleaning import clean_power
from cycling_coach.physiology import (
    build_cp_observations,
    build_cp_observations_from_mmp,
)
from cycling_coach.physiology.critical_power import CP_FIT_DURATIONS
from cycling_coach.twin.mmp_service import MMP_DURATIONS, compute_mmp


def _ride(seed: int, n: int = 4000) -> list[float]:
    """Serie de potencia sintética con un esfuerzo fuerte en medio."""
    base = [150.0 + (i * seed) % 40 for i in range(n)]
    for i in range(1000, 1000 + 600):        # 10 min duros
        base[i] = 320.0 + (i % 7)
    for i in range(2500, 2500 + 120):        # 2 min muy duros
        base[i] = 420.0 + (i % 5)
    return base


def test_stored_mmp_matches_direct_computation():
    watts = _ride(3)
    raw, clean = compute_mmp(watts)
    esperado_raw = mean_maximal_power(watts, MMP_DURATIONS)
    esperado_clean = mean_maximal_power(clean_power(watts, 1.0), MMP_DURATIONS)
    assert {int(k): v for k, v in raw.items()} == esperado_raw
    assert {int(k): v for k, v in clean.items()} == esperado_clean


def test_raw_and_clean_are_kept_apart():
    """Guardar una sola variante cambiaría en silencio el veredicto de
    coherencia, que trabaja sobre la señal CRUDA."""
    watts = _ride(5)
    watts[2000] = 3000.0                      # pico imposible: la limpieza lo quita
    raw, clean = compute_mmp(watts)
    assert raw["5"] > clean["5"], "la limpieza debe recortar el pico"


def test_mmp_covers_every_duration_the_code_uses():
    """Quedarse corto en duraciones rompería un consumidor en silencio."""
    from cycling_coach.metrics.power import DEFAULT_DURATIONS_S
    from cycling_coach.twin.coherence_service import (
        _CURVE_DURATIONS_S,
        _DURATIONS_S,
    )

    necesarias = (
        set(CP_FIT_DURATIONS) | set(DEFAULT_DURATIONS_S)
        | set(_DURATIONS_S) | set(_CURVE_DURATIONS_S) | {1200}
    )
    assert necesarias <= set(MMP_DURATIONS)


def test_observations_from_mmp_equal_observations_from_streams():
    """El criterio de éxito del paso: mismo resultado por los dos caminos."""
    acts = [
        (datetime(2026, 1, 1 + i, tzinfo=UTC), i, _ride(i + 1))
        for i in range(6)
    ]
    desde_streams = build_cp_observations(acts)
    desde_mmp = build_cp_observations_from_mmp([
        (when, key, mean_maximal_power(clean_power(w, 1.0), CP_FIT_DURATIONS))
        for when, key, w in acts
    ])
    assert len(desde_streams) == len(desde_mmp)
    for a, b in zip(desde_streams, desde_mmp, strict=True):
        assert a.when == b.when
        assert a.cp == b.cp
        assert a.w_prime == b.w_prime
        assert a.sd_cp == b.sd_cp


def test_a_superset_of_durations_is_accepted():
    """La MMP guardada trae 15 duraciones; el filtro solo mira las suyas."""
    acts = [
        (datetime(2026, 1, 1 + i, tzinfo=UTC), i, _ride(i + 1))
        for i in range(6)
    ]
    completa = build_cp_observations_from_mmp([
        (when, key, mean_maximal_power(clean_power(w, 1.0), MMP_DURATIONS))
        for when, key, w in acts
    ])
    justa = build_cp_observations_from_mmp([
        (when, key, mean_maximal_power(clean_power(w, 1.0), CP_FIT_DURATIONS))
        for when, key, w in acts
    ])
    assert [o.cp for o in completa] == [o.cp for o in justa]


def test_empty_input_is_not_an_error():
    assert build_cp_observations_from_mmp([]) == []
