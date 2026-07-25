"""Flujo OAuth2 de Strava (authorization code + refresh).

Docs: https://developers.strava.com/docs/authentication/
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"

# `activity:read_all` incluye actividades privadas; `read` da perfil básico.
DEFAULT_SCOPES = ("read", "activity:read_all")


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: datetime          # tz-aware UTC
    athlete_id: str | None = None
    scope: str | None = None

    @property
    def is_expired(self) -> bool:
        # Margen de 60 s para no usar un token a punto de caducar.
        return datetime.now(UTC).timestamp() >= (self.expires_at.timestamp() - 60)


def build_authorize_url(
    client_id: str, redirect_uri: str, scopes: tuple[str, ...] = DEFAULT_SCOPES
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": ",".join(scopes),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _to_tokenset(payload: dict) -> TokenSet:
    athlete = payload.get("athlete") or {}
    return TokenSet(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        expires_at=datetime.fromtimestamp(payload["expires_at"], tz=UTC),
        athlete_id=str(athlete["id"]) if athlete.get("id") is not None else None,
        scope=payload.get("scope"),
    )


def exchange_code(client_id: str, client_secret: str, code: str) -> TokenSet:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return _to_tokenset(resp.json())


def refresh_tokens(client_id: str, client_secret: str, refresh_token: str) -> TokenSet:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    # El refresh no devuelve `athlete`; conservamos el id previo aparte.
    return _to_tokenset(resp.json())
