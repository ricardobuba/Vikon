"""Cliente HTTP de la Strava API v3 con refresco de token y control de rate limit.

Límites por defecto (app no ampliada): 200 req / 15 min y 2000 req / día.
Docs: https://developers.strava.com/docs/rate-limits/
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx

from cycling_coach.adapters.strava.oauth import TokenSet, refresh_tokens

API_BASE = "https://www.strava.com/api/v3"


class StravaRateLimitError(RuntimeError):
    """Se agotó el límite de peticiones (429) y no procede esperar más."""


class StravaClient:
    """Cliente autenticado. Refresca el token de forma transparente y, cuando
    lo hace, avisa vía `on_token_refresh` para poder persistirlo."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        tokens: TokenSet,
        on_token_refresh: Callable[[TokenSet], None] | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._tokens = tokens
        self._on_refresh = on_token_refresh
        self._http = httpx.Client(base_url=API_BASE, timeout=60)

    # -- ciclo de vida -------------------------------------------------------
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> StravaClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- autenticación -------------------------------------------------------
    def _auth_header(self) -> dict[str, str]:
        if self._tokens.is_expired:
            new = refresh_tokens(
                self._client_id, self._client_secret, self._tokens.refresh_token
            )
            # El refresh no reenvía athlete_id; conservamos el que ya teníamos.
            if new.athlete_id is None:
                new.athlete_id = self._tokens.athlete_id
            self._tokens = new
            if self._on_refresh:
                self._on_refresh(new)
        return {"Authorization": f"Bearer {self._tokens.access_token}"}

    # -- petición con reintento por rate limit -------------------------------
    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        for attempt in range(2):
            resp = self._http.get(path, params=params, headers=self._auth_header())
            if resp.status_code == 429:
                wait = _seconds_until_next_quarter_hour(resp)
                if attempt == 0 and wait <= 15 * 60:
                    time.sleep(wait + 1)
                    continue
                raise StravaRateLimitError(
                    f"Rate limit alcanzado en {path}. Reintenta en ~{wait} s."
                )
            resp.raise_for_status()
            return resp
        raise StravaRateLimitError(f"Rate limit persistente en {path}.")

    # -- endpoints -----------------------------------------------------------
    def iter_raw_activities(
        self,
        after: datetime | None = None,
        before: datetime | None = None,
        per_page: int = 200,
    ) -> Iterator[dict]:
        """Pagina /athlete/activities de más antigua a más reciente."""
        page = 1
        params: dict[str, int] = {"per_page": per_page}
        if after is not None:
            params["after"] = int(after.timestamp())
        if before is not None:
            params["before"] = int(before.timestamp())
        while True:
            batch = self._get("/athlete/activities", {**params, "page": page}).json()
            if not batch:
                return
            yield from batch
            if len(batch) < per_page:
                return
            page += 1

    def get_raw_athlete(self) -> dict:
        """Perfil del atleta autenticado (GET /athlete)."""
        return self._get("/athlete").json()

    def get_raw_streams(self, activity_id: str) -> dict:
        keys = (
            "time,watts,heartrate,cadence,velocity_smooth,"
            "altitude,distance,temp,moving,grade_smooth"
        )
        try:
            resp = self._get(
                f"/activities/{activity_id}/streams",
                {"keys": keys, "key_by_type": "true"},
            )
        except httpx.HTTPStatusError as exc:
            # 404 = la actividad no tiene streams (entrada manual, sin sensores).
            # Es esperado para actividades antiguas; no es un error.
            if exc.response.status_code == 404:
                return {}
            raise
        return resp.json()


def _seconds_until_next_quarter_hour(resp: httpx.Response) -> int:
    """Estima la espera hasta que se reinicie la ventana de 15 min de Strava."""
    # Strava reinicia los contadores de 15 min en :00, :15, :30, :45.
    date_hdr = resp.headers.get("Date")
    try:
        now = parsedate_to_datetime(date_hdr) if date_hdr else None
    except (TypeError, ValueError):
        now = None
    if now is None:
        return 15 * 60
    seconds_into = (now.minute % 15) * 60 + now.second
    return max(1, 15 * 60 - seconds_into)
