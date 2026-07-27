"""Configuración cargada desde variables de entorno / fichero .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,     # permite construir por nombre de campo (tests)
    )

    database_url: str = "postgresql+psycopg://cc:cc@localhost:5432/cycling_coach"

    strava_client_id: str | None = None
    strava_client_secret: str | None = None
    oauth_port: int = 8721

    # --- Capa conversacional (cap. 11) --------------------------------------
    # Agnóstica del proveedor: cualquier endpoint OpenAI-compatible. Cambiar de
    # LLM (gratis → Claude) = cambiar estas 3 variables en el .env, sin código.
    #   Groq:      https://api.groq.com/openai/v1        (gratis, rápido)
    #   Gemini:    https://generativelanguage.googleapis.com/v1beta/openai
    #   OpenRouter https://openrouter.ai/api/v1
    #   Ollama:    http://localhost:11434/v1             (local, sin clave)
    #   Anthropic: https://api.anthropic.com/v1          (de pago)
    llm_base_url: str = "https://api.groq.com/openai/v1"
    # Acepta el nombre genérico o el habitual de cada proveedor, para que la
    # clave "simplemente funcione" la pongas como la pongas.
    llm_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LLM_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
            "GROQ_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
        ),
    )
    llm_model: str = "llama-3.3-70b-versatile"
    llm_temperature: float = 0.4

    # Sincronización automática con Strava mientras el servidor corre (segundos).
    # 0 = desactivada (solo al abrir la app / `cc sync`).
    sync_interval_s: int = 900     # 15 min

    # Pantalla de acceso (cuentas). PESTILLO DE SEGURIDAD: si algo se tuerce y te
    # deja fuera, pon AUTH_ENABLED=false en el .env y vuelves a entrar sin login
    # (usa el primer atleta, como antes).
    auth_enabled: bool = True

    @property
    def strava_redirect_uri(self) -> str:
        return f"http://localhost:{self.oauth_port}/callback"

    @property
    def llm_configured(self) -> bool:
        # Ollama local no necesita clave; el resto sí.
        return bool(self.llm_api_key) or "localhost" in self.llm_base_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
