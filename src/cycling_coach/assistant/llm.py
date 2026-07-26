"""Cliente LLM agnóstico del proveedor (endpoint OpenAI-compatible).

Habla el formato `/chat/completions` que exponen Groq, Gemini, OpenRouter,
Ollama y Anthropic. Cambiar de proveedor = cambiar base_url/api_key/model en el
.env, sin tocar este código. Solo depende de httpx (ya en el stack).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from cycling_coach.config import Settings, get_settings


class LLMError(RuntimeError):
    """Fallo al hablar con el proveedor de LLM (sin clave, red, respuesta rara)."""


@dataclass
class LLMClient:
    base_url: str
    model: str
    api_key: str | None = None
    temperature: float = 0.4
    timeout: float = 60.0

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> LLMClient:
        s = settings or get_settings()
        if not s.llm_configured:
            raise LLMError(
                "LLM sin configurar. Añade LLM_API_KEY (y opcional LLM_BASE_URL / "
                "LLM_MODEL) al .env. Groq gratis: https://console.groq.com"
            )
        return cls(
            base_url=s.llm_base_url.rstrip("/"),
            model=s.llm_model,
            api_key=s.llm_api_key,
            temperature=s.llm_temperature,
        )

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> str:
        """Una vuelta system+user → texto de la respuesta. `json_mode` pide al
        proveedor que devuelva JSON (lo soportan Groq/OpenAI/Gemini)."""
        return self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
            json_mode=json_mode,
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> str:
        """Conversación multi-mensaje (roles system/user/assistant) → texto."""
        payload: dict = {
            "model": self.model,
            "temperature": self.temperature if temperature is None else temperature,
            "messages": messages,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:                       # red, timeout, DNS
            raise LLMError(f"No pude contactar al LLM: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"El LLM respondió {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"Respuesta del LLM inesperada: {resp.text[:300]}") from exc

    def extract_json(self, system: str, user: str) -> dict:
        """Como `complete` pero parsea la respuesta como JSON (tolerante a que el
        modelo la envuelva en ```json ... ```)."""
        raw = self.complete(system, user, temperature=0.0, json_mode=True)
        return _parse_json_lenient(raw)


def _parse_json_lenient(raw: str) -> dict:
    """Extrae el primer objeto JSON de un texto (por si el modelo lo adorna)."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{") : raw.rfind("}") + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise LLMError(f"El LLM no devolvió JSON válido: {raw[:200]}") from exc
