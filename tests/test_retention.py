"""Purga de datos crudos de Strava al desconectar (BLINDAJE_LEGAL_Plan.md #3).

Sin fixture de Postgres en este repo (los demás tests son unitarios/con
mocks): en vez de ejecutar SQL de verdad, compilamos las sentencias que
generan `delete_streams_for_athlete`/`clear_activity_raw_payloads` y
verificamos su FORMA — que tocan la tabla correcta, filtran por atleta, y
(crucial) que la purga de streams NO toca `activity` y la del JSON crudo
NO toca las columnas tipadas que el motor de CTL/ATL/TSB necesita.
"""

from __future__ import annotations

from cycling_coach import accounts as A
from cycling_coach.db.repositories import (
    clear_activity_raw_payloads,
    delete_streams_for_athlete,
)


class _FakeResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _RecordingSession:
    """No toca BD: guarda las sentencias que se ejecutarían y responde con un
    rowcount fijo, para poder inspeccionar SQL sin Postgres."""

    def __init__(self, rowcount: int = 3):
        self.executed: list = []
        self._rowcount = rowcount

    def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeResult(self._rowcount)

    def flush(self):
        pass


def test_delete_streams_targets_stream_table_scoped_by_athlete():
    session = _RecordingSession(rowcount=5)
    n = delete_streams_for_athlete(session, athlete_id=42)

    assert n == 5
    stmt = session.executed[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "DELETE FROM stream" in sql
    assert "activity_id IN" in sql   # subconsulta por athlete_id, no borra a lo loco


def test_clear_activity_raw_only_touches_raw_column():
    session = _RecordingSession(rowcount=7)
    n = clear_activity_raw_payloads(session, athlete_id=42)

    assert n == 7
    stmt = session.executed[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "UPDATE activity SET raw=" in sql
    assert "athlete_id" in sql
    # No debe tocar columnas de rendimiento que usa el motor de CTL/ATL/TSB.
    for typed_col in ("avg_power_w", "weighted_avg_power_w", "distance_m", "start_time"):
        assert typed_col not in sql


def test_purge_raw_strava_data_calls_both_and_aggregates(monkeypatch):
    calls: list[tuple[str, int]] = []

    def _fake_streams(session, athlete_id):
        calls.append(("streams", athlete_id))
        return 12

    def _fake_raw(session, athlete_id):
        calls.append(("raw", athlete_id))
        return 340

    monkeypatch.setattr(A, "delete_streams_for_athlete", _fake_streams)
    monkeypatch.setattr(A, "clear_activity_raw_payloads", _fake_raw)

    result = A.purge_raw_strava_data(_RecordingSession(), athlete_id=7)

    assert result == {"streams_deleted": 12, "activities_cleared": 340}
    assert calls == [("streams", 7), ("raw", 7)]
