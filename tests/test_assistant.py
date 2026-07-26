"""Tests de la capa conversacional (con LLM simulado: sin clave ni red)."""

from __future__ import annotations

from datetime import date

import pytest

from cycling_coach.assistant.assistant import Intent, parse_intent
from cycling_coach.assistant.grounding import Facts
from cycling_coach.assistant.llm import LLMClient, LLMError, _parse_json_lenient
from cycling_coach.config import Settings


class _StubLLM:
    """LLM de mentira: devuelve un JSON fijo y un texto fijo."""

    def __init__(self, payload: dict, text: str = "explicación"):
        self.payload = payload
        self.text = text
        self.seen: list[str] = []

    def extract_json(self, system: str, user: str) -> dict:
        self.seen.append(user)
        return self.payload

    def complete(self, system: str, user: str, **kw) -> str:
        self.seen.append(user)
        return self.text


# --- Cliente: parseo tolerante y configuración ------------------------------
def test_parse_json_lenient_handles_fences():
    assert _parse_json_lenient('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_lenient('bla {"a": 2} fin') == {"a": 2}


def test_llm_requires_config():
    s = Settings(
        _env_file=None, llm_api_key=None, llm_base_url="https://api.groq.com/openai/v1"
    )
    with pytest.raises(LLMError):
        LLMClient.from_settings(s)


def test_ollama_local_needs_no_key():
    s = Settings(_env_file=None, llm_api_key=None, llm_base_url="http://localhost:11434/v1")
    assert s.llm_configured
    LLMClient.from_settings(s)          # no lanza


# --- Traducción de intención -------------------------------------------------
def test_intent_plan_with_minutes_and_readiness():
    llm = _StubLLM({"kind": "plan", "minutes": 40, "readiness": "low", "question": None})
    it = parse_intent(llm, "solo tengo 40 min y estoy reventado")
    assert it.kind == "plan"
    assert it.minutes == 40
    assert it.readiness == "low"
    assert it.cri_override == 30.0          # cansado → cruza el umbral de recovery


def test_intent_high_readiness_maps_to_high_cri():
    it = parse_intent(_StubLLM({"kind": "plan", "readiness": "high"}), "me siento fuerte")
    assert it.cri_override == 80.0


def test_intent_question_kind():
    llm = _StubLLM({"kind": "question", "question": "¿por qué descanso?"})
    it = parse_intent(llm, "¿por qué me toca descansar?")
    assert it.kind == "question"
    assert it.question == "¿por qué descanso?"
    assert it.minutes is None and it.cri_override is None


def test_intent_ignores_garbage_readiness():
    it = parse_intent(_StubLLM({"kind": "plan", "readiness": "meh"}), "x")
    assert it.readiness is None and it.cri_override is None


# --- Ficha de hechos ---------------------------------------------------------
def test_facts_prompt_contains_key_numbers():
    f = Facts(as_of=date(2026, 7, 26), ftp=348.0, tsb=-17.8, cri=62.0, cri_coverage=0.75)
    block = f.to_prompt()
    assert "FTP: 348 W" in block
    assert "TSB): -17.8" in block
    assert "62/100" in block
    assert "Meta: ninguna registrada" in block


def test_facts_prompt_shows_goal_and_phase():
    f = Facts(
        as_of=date(2026, 7, 26), goal_date=date(2026, 8, 25),
        goal_name="Test", days_to_event=30, phase="peak",
    )
    assert "faltan 30 días, fase peak" in f.to_prompt()


def test_intent_cri_override_normal_is_none():
    assert Intent(readiness="normal").cri_override is None
    assert Intent(readiness=None).cri_override is None


def test_facts_shows_subjective_cri_override():
    f = Facts(as_of=date(2026, 7, 26), cri=62.0, subjective_cri=80.0)
    block = f.to_prompt()
    assert "calculada (CRI): 62/100" in block
    assert "dijiste hoy y que USÓ el plan: 80/100" in block


# --- Chat multivuelta con intención pegajosa (sin DB: LLM y facts simulados) --
def test_chat_sticky_intent(monkeypatch):
    from cycling_coach.assistant import assistant as A

    # Guiones de intención por turno (el stub devuelve el siguiente cada vez).
    intents = [
        {"kind": "plan", "minutes": 40, "readiness": None},
        {"kind": "question", "minutes": None, "readiness": None, "question": "¿por qué?"},
        {"kind": "plan", "minutes": None, "readiness": "high"},
    ]
    calls = {"minutes": [], "cri": []}

    class _LLM:
        def extract_json(self, system, user):
            return intents.pop(0)

        def chat(self, messages, **kw):
            return "ok"

    def _fake_gather(session, athlete_id, as_of, *, minutes=None, cri_override=None):
        calls["minutes"].append(minutes)
        calls["cri"].append(cri_override)
        return Facts(as_of=as_of)

    monkeypatch.setattr(A, "gather_facts", _fake_gather)
    chat = A.ChatSession(session=None, athlete_id=1, as_of=date(2026, 7, 26), llm=_LLM())

    chat.turn("solo tengo 40 min")
    chat.turn("¿por qué?")            # sin minutos nuevos → 40 pegajoso
    chat.turn("y si me siento fuerte")  # readiness high, 40 sigue

    assert calls["minutes"] == [40.0, 40.0, 40.0]        # minutos persisten
    assert calls["cri"] == [None, None, 80.0]            # readiness solo al final
    assert len(chat.history) == 6                        # 3 turnos (user+assistant)
