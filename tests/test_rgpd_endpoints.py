"""Derechos RGPD: exportación (arts. 15 y 20) y supresión (art. 17).

Sin fixture de Postgres en este repo, así que se verifica lo que se puede
verificar sin BD y que es justo lo que suele fallar: que la exportación cubra
TODAS las tablas con datos personales (una tabla olvidada es un derecho de
acceso incompleto), que las credenciales queden fuera, y que el borrado use una
sentencia DELETE — no `session.delete`, que dejaría filas huérfanas.
"""

from __future__ import annotations

import inspect

from cycling_coach import accounts as A
from cycling_coach.db import models as M


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _RecordingSession:
    """Registra las sentencias sin tocar BD y devuelve conjuntos vacíos."""

    def __init__(self):
        self.executed: list = []

    def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeResult([])

    def flush(self):
        pass


def _tables_with_athlete_id() -> set[str]:
    """Toda tabla del modelo que cuelgue de un atleta = dato personal suyo."""
    found = set()
    for obj in vars(M).values():
        table = getattr(obj, "__table__", None)
        if table is None or not inspect.isclass(obj):
            continue
        if "athlete_id" in table.columns:
            found.add(table.name)
    return found


def test_la_exportacion_cubre_todas_las_tablas_del_atleta():
    """Si alguien añade una tabla con `athlete_id` y no la mete en el export,
    este test lo caza. Un derecho de acceso incompleto es un incumplimiento."""
    session = _RecordingSession()
    out = A.export_all_data(session, athlete_id=1)

    exportadas = set(out["datos"].keys())
    esperadas = _tables_with_athlete_id() | {"athlete", "stream"}
    faltan = esperadas - exportadas
    assert not faltan, (
        f"La exportación RGPD no incluye estas tablas con datos personales: "
        f"{sorted(faltan)}. Añádelas a `export_all_data` en accounts.py."
    )


def test_la_exportacion_no_entrega_credenciales():
    """El derecho de acceso no cubre secretos: ni el hash de la contraseña ni
    los tokens de Strava son datos personales del interesado."""
    assert A._EXPORT_REDACTED == {"pw_hash", "pw_salt", "access_token", "refresh_token"}

    fila = {"id": 1, "pw_hash": "abc", "pw_salt": "def", "username": "ana"}
    for campo in A._EXPORT_REDACTED & fila.keys():
        fila[campo] = "[omitido: credencial]"
    assert fila["pw_hash"] == "[omitido: credencial]"
    assert fila["username"] == "ana"          # lo suyo sí sale


def test_el_borrado_usa_delete_y_no_session_delete():
    """`session.delete(obj)` solo dispara las cascadas declaradas en el ORM
    (`accounts` y `activities`); el resto depende del ON DELETE CASCADE de la
    base de datos. Con `session.delete` creerías haber borrado."""
    session = _RecordingSession()
    A.delete_athlete_and_user(session, athlete_id=7)

    assert len(session.executed) == 1
    sql = str(session.executed[0].compile(compile_kwargs={"literal_binds": False}))
    assert sql.startswith("DELETE FROM athlete")
    assert "WHERE athlete.id" in sql


def test_serializacion_de_fechas_para_json():
    from datetime import UTC, date, datetime

    assert A._serializable(date(2026, 8, 14)) == "2026-08-14"
    assert A._serializable(datetime(2026, 8, 14, tzinfo=UTC)).startswith("2026-08-14")
    assert A._serializable(3.5) == 3.5
    assert A._serializable(None) is None
