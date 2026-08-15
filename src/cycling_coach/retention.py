"""Política de retención de datos, en un solo sitio y ejecutable.

Estaba repartida entre un `purge_old_chat` que solo se disparaba si alguien
abría el chat y un `delete_streams_older_than` que no llamaba nadie. Eso no es
una política de retención: es una casualidad. Aquí vive la política entera,
con sus plazos y su porqué, y `apply_retention` la ejecuta de verdad.

Plazos y de dónde salen:

- **Mensajes de chat — 7 días.** Decisión nuestra, ya vigente (`CHAT_MEMORY_DAYS`).
  El chat guarda texto libre: lesiones, estados de ánimo, nombres de terceros.
  Cuanto menos tiempo viva, mejor.
- **Streams (series a 1 Hz) — configurable, por defecto 12 meses.** Aquí hay una
  tensión REAL que conviene no barrer: la API Policy de Strava (§6.2) limita la
  caché de datos de Strava a 7 días, pero el stream crudo es lo que permite
  clasificar zonas e intervalos de una sesión (`metrics/session_type.py`), así
  que bajar a 7 días apaga el detalle por zonas de todo el histórico. El motor
  de CP no se ve afectado (consume `activity_mmp`, no el stream). Es una
  decisión de producto con coste funcional, no una constante técnica: se ajusta
  con `STREAM_RETENTION_DAYS` en el .env. Ver BLINDAJE_LEGAL_Plan.md §3.
- **`activity.raw` — no se guarda.** Desde el cambio en `adapters/strava/mapper.py`
  el JSON crudo (GPS, polilínea, texto libre) ya no entra. `purge_raw_strava_data`
  limpia lo que quedara de antes.

Lo que NO se purga por tiempo, y por qué: las columnas tipadas de `activity`
(potencia media, duración, fecha) y `activity_mmp`. El motor de CTL/ATL/TSB y
los umbrales personalizados los leen en vivo sobre TODA la historia — es la
decisión de "usa todos los datos" que se tomó para que un año flojo no borre la
capacidad de fondo. Son el historial de rendimiento del atleta. Se borran
enteros al cerrar la cuenta (`accounts.purge_raw_strava_data` /
`delete_athlete_and_user`), no por antigüedad.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from cycling_coach.config import get_settings
from cycling_coach.db.repositories import (
    athlete_ids_with_activities,
    delete_streams_older_than,
    purge_old_chat,
)

_log = logging.getLogger("uvicorn.error")

# Retención del historial de chat, en días. Coincide con `CHAT_MEMORY_DAYS` de
# assistant.py: allí acota lo que Vikon RECUERDA, aquí lo que se CONSERVA.
CHAT_RETENTION_DAYS = 7


@dataclass
class RetentionReport:
    """Qué se purgó. Se registra en el log para que la política sea auditable."""

    chat_purged_for: int = 0        # atletas a los que se les podó el chat
    streams_deleted: int = 0
    stream_window_days: int = 0

    def is_empty(self) -> bool:
        return self.streams_deleted == 0 and self.chat_purged_for == 0


def stream_retention_days() -> int:
    """Ventana de conservación de streams. Configurable porque es una decisión
    de producto con coste funcional (ver el docstring del módulo)."""
    return get_settings().stream_retention_days


def apply_retention(session: Session, *, now: datetime | None = None) -> RetentionReport:
    """Ejecuta la política sobre TODOS los perfiles. Idempotente: repetirla no
    hace daño, y si no hay nada que purgar no toca nada."""
    now = now or datetime.now(UTC)
    window = stream_retention_days()
    report = RetentionReport(stream_window_days=window)

    chat_before = now - timedelta(days=CHAT_RETENTION_DAYS)
    stream_before = (now - timedelta(days=window)).date()

    for aid in athlete_ids_with_activities(session):
        purge_old_chat(session, aid, chat_before)
        report.chat_purged_for += 1
        report.streams_deleted += delete_streams_older_than(session, aid, stream_before)

    session.flush()
    return report


def run_retention(session: Session) -> RetentionReport:
    """Como `apply_retention`, pero deja constancia en el log. Es la que llama
    el bucle del servidor: una purga silenciosa no se puede demostrar."""
    report = apply_retention(session)
    if not report.is_empty():
        _log.info(
            "Retención aplicada: %d streams borrados (>%d días), chat podado en "
            "%d perfiles (>%d días).",
            report.streams_deleted, report.stream_window_days,
            report.chat_purged_for, CHAT_RETENTION_DAYS,
        )
    return report
