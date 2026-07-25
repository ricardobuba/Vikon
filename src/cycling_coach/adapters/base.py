"""Contrato que todo adaptador de proveedor debe cumplir.

Mantener esta interfaz estrecha permite añadir Intervals.icu, Garmin o Wahoo
sin tocar la ingesta ni el gemelo (cap. 13.2). El gemelo consume SOLO tipos
canónicos; los proveedores viven detrás de este `Protocol`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from cycling_coach.domain.models import CanonicalActivity, CanonicalStream


@runtime_checkable
class ActivitySource(Protocol):
    #: Identificador del proveedor, p. ej. "strava".
    provider: str

    def iter_activities(
        self, after: datetime | None = None, before: datetime | None = None
    ) -> Iterator[CanonicalActivity]:
        """Itera actividades en el rango [after, before], ya normalizadas."""
        ...

    def get_streams(self, provider_activity_id: str) -> list[CanonicalStream]:
        """Devuelve las series temporales de una actividad."""
        ...
