"""Configuración cargada desde variables de entorno / fichero .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://cc:cc@localhost:5432/cycling_coach"

    strava_client_id: str | None = None
    strava_client_secret: str | None = None
    oauth_port: int = 8721

    @property
    def strava_redirect_uri(self) -> str:
        return f"http://localhost:{self.oauth_port}/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
