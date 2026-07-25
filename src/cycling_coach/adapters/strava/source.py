"""`StravaSource`: implementación de `ActivitySource` que combina el cliente
HTTP con el mapeo canónico. Es lo único que la ingesta necesita conocer."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from cycling_coach.adapters.strava.client import StravaClient
from cycling_coach.adapters.strava.mapper import map_activity, map_athlete, map_streams
from cycling_coach.domain.models import CanonicalActivity, CanonicalStream


class StravaSource:
    provider = "strava"

    def __init__(self, client: StravaClient) -> None:
        self._client = client

    def iter_activities(
        self, after: datetime | None = None, before: datetime | None = None
    ) -> Iterator[CanonicalActivity]:
        for raw in self._client.iter_raw_activities(after=after, before=before):
            yield map_activity(raw)

    def get_streams(self, provider_activity_id: str) -> list[CanonicalStream]:
        raw = self._client.get_raw_streams(provider_activity_id)
        return map_streams(raw)

    def get_athlete_profile(self) -> dict:
        """Perfil estático (semilla): {name?, sex?, weight_kg?}."""
        return map_athlete(self._client.get_raw_athlete())
