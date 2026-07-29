"""Tests del cumplimiento del plan (prescrito vs hecho)."""

from __future__ import annotations

from cycling_coach.twin.compliance import _status


def test_followed_when_type_and_load_match():
    st, _ = _status("sweet_spot", 70.0, "sweet_spot", 72.0)
    assert st == "cumplido"


def test_adjacent_type_still_counts_as_followed():
    """Un sweet spot que acaba en umbral es el mismo trabajo de calidad."""
    st, _ = _status("sweet_spot", 70.0, "threshold", 74.0)
    assert st == "cumplido"


def test_very_different_type_is_flagged():
    st, note = _status("recovery", 23.0, "vo2max", 90.0)
    assert st == "distinto" and "vo2max" in note


def test_more_and_less_load():
    assert _status("endurance", 60.0, "endurance", 100.0)[0] == "más"
    assert _status("endurance", 60.0, "endurance", 30.0)[0] == "menos"


def test_planned_but_not_done():
    st, _ = _status("threshold", 70.0, None, None)
    assert st == "no_hecho"


def test_rest_respected_and_broken():
    assert _status("rest", None, None, None)[0] == "descanso_ok"
    assert _status("rest", None, "endurance", 60.0)[0] == "extra"


def test_unplanned_session_is_extra_not_failure():
    st, _ = _status(None, None, "endurance", 60.0)
    assert st == "extra"


def test_no_plan_no_session_is_fine():
    assert _status(None, None, None, None)[0] == "descanso_ok"
