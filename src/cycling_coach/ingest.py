"""Orquestación de la ingesta: recorre un `ActivitySource` y persiste al modelo
canónico de forma idempotente. Sirve tanto para backfill histórico como para
la sincronización incremental (cap. 13.2)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from cycling_coach.adapters.base import ActivitySource
from cycling_coach.db.engine import session_scope
from cycling_coach.db.repositories import (
    activity_exists,
    upsert_activity,
    upsert_stream,
)
from cycling_coach.domain.models import CanonicalActivity
from cycling_coach.twin.mmp_service import store_mmp


@dataclass
class BackfillResult:
    activities_seen: int = 0
    activities_ingested: int = 0
    streams_ingested: int = 0
    skipped_existing: int = 0
    stream_errors: int = 0


def backfill(
    source: ActivitySource,
    athlete_id: int,
    *,
    after: datetime | None = None,
    before: datetime | None = None,
    fetch_streams: bool = True,
    skip_existing: bool = True,
    on_progress: Callable[[CanonicalActivity, str], None] | None = None,
) -> BackfillResult:
    """Importa actividades (y sus streams) desde `source` al gemelo.

    `skip_existing`: si la actividad ya está en BD, no se vuelve a pedir sus
    streams (ahorra cuota de API). Poner a False para reprocesar todo.
    """
    result = BackfillResult()

    for act in source.iter_activities(after=after, before=before):
        result.activities_seen += 1

        # 1) La actividad se persiste en su propia transacción: aunque luego
        #    falle la descarga de streams, la actividad ya queda guardada.
        with session_scope() as session:
            if skip_existing and activity_exists(
                session, source.provider, act.provider_activity_id
            ):
                result.skipped_existing += 1
                if on_progress:
                    on_progress(act, "skip")
                continue
            activity_id = upsert_activity(session, athlete_id, act)
            result.activities_ingested += 1

        # 2) Streams por separado y con aislamiento de errores: un fallo puntual
        #    (timeout, 5xx, actividad rara) no debe abortar todo el backfill.
        if fetch_streams:
            try:
                streams = source.get_streams(act.provider_activity_id)
            except Exception:  # noqa: BLE001 — robustez del backfill; se contabiliza
                result.stream_errors += 1
                streams = []
            if streams:
                with session_scope() as session:
                    watts = None
                    for stream in streams:
                        upsert_stream(session, activity_id, stream)
                        result.streams_ingested += 1
                        if stream.stream_type.value == "watts":
                            watts = stream.data
                    # La MMP se calcula UNA vez, aquí. Es lo que consume el motor
                    # de CP, y derivarla luego obligaría a releer la serie entera.
                    if watts and act.device_watts:
                        store_mmp(
                            session, athlete_id, activity_id, act.start_time, watts
                        )

        if on_progress:
            on_progress(act, "ingested")

    return result
