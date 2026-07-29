"""Tests del cumplimiento del plan (prescrito vs hecho)."""

from __future__ import annotations

from datetime import date

from cycling_coach.twin.compliance import (
    MIN_DAYS_FOR_SCORE,
    ComplianceReport,
    DayCompliance,
    _status,
)


def _report(*statuses: str) -> ComplianceReport:
    days = [
        DayCompliance(
            day=date(2026, 7, 1 + i), planned_objective="endurance", planned_tss=60.0,
            done_kind="endurance", done_tss=60.0, done_minutes=90.0,
            status=st, note="",
        )
        for i, st in enumerate(statuses)
    ]
    return ComplianceReport(
        days=days, rate=0.0, n_planned=len(days), n_followed=0,
        tss_planned=0.0, tss_done=0.0,
    )


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


# --- Puntuación para el CRI --------------------------------------------------
def test_score_is_one_when_everything_followed():
    assert _report("cumplido", "descanso_ok", "cumplido").score == 1.0


def test_score_is_zero_when_nothing_done():
    assert _report("no_hecho", "no_hecho", "no_hecho").score == 0.0


def test_training_something_else_beats_not_training():
    """Entrenar algo distinto NO es igual que no entrenar: crédito parcial."""
    otra = _report("distinto", "distinto", "distinto").score
    nada = _report("no_hecho", "no_hecho", "no_hecho").score
    assert nada < otra < 1.0


def test_score_absent_with_too_few_days():
    """Con pocos días el ratio no dice nada: se declara ausente, no se inventa."""
    assert _report(*["cumplido"] * (MIN_DAYS_FOR_SCORE - 1)).score is None
    assert _report(*["cumplido"] * MIN_DAYS_FOR_SCORE).score is not None
