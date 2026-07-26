"""Orquestador conversacional: intención → planner determinista → explicación.

El LLM aparece solo en los extremos (traducir la intención, redactar la
respuesta). La decisión —qué entrenar— siempre la toma el motor con la ficha.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from cycling_coach.assistant.grounding import Facts, gather_facts
from cycling_coach.assistant.llm import LLMClient, LLMError
from cycling_coach.assistant.prompts import EXPLAIN_SYSTEM, INTENT_SYSTEM, explain_user

# Disposición subjetiva → CRI efectivo (determinista). "low" cruza el umbral de
# recovery (<40); "high" habilita calidad alta si además la forma acompaña.
_READINESS_CRI = {"low": 30.0, "high": 80.0}


@dataclass
class Intent:
    kind: str = "plan"                 # "plan" | "question"
    minutes: float | None = None
    readiness: str | None = None       # "low" | "normal" | "high"
    question: str | None = None

    @property
    def cri_override(self) -> float | None:
        return _READINESS_CRI.get(self.readiness or "")


@dataclass
class Reply:
    text: str                          # respuesta en lenguaje natural
    intent: Intent
    facts: Facts


def parse_intent(llm: LLMClient, message: str) -> Intent:
    """El LLM traduce el texto libre a inputs estructurados (no decide nada)."""
    data = llm.extract_json(INTENT_SYSTEM, message)
    kind = data.get("kind")
    minutes = data.get("minutes")
    readiness = data.get("readiness")
    return Intent(
        kind="question" if kind == "question" else "plan",
        minutes=float(minutes) if isinstance(minutes, int | float) else None,
        readiness=readiness if readiness in ("low", "normal", "high") else None,
        question=data.get("question") or None,
    )


def ask(
    session: Session,
    athlete_id: int,
    as_of: date,
    message: str,
    llm: LLMClient | None = None,
) -> Reply:
    """Una vuelta completa: interpreta el mensaje, deja que el motor decida con
    esos inputs, y redacta la respuesta anclada a la ficha."""
    llm = llm or LLMClient.from_settings()
    intent = parse_intent(llm, message)

    facts = gather_facts(
        session, athlete_id, as_of,
        minutes=intent.minutes, cri_override=intent.cri_override,
    )
    question = intent.question if intent.kind == "question" else None
    text = llm.complete(EXPLAIN_SYSTEM, explain_user(facts.to_prompt(), question))
    return Reply(text=text.strip(), intent=intent, facts=facts)


def explain_today(
    session: Session, athlete_id: int, as_of: date, llm: LLMClient | None = None
) -> Reply:
    """Narra el plan de hoy en lenguaje natural (sin traducir intención)."""
    llm = llm or LLMClient.from_settings()
    facts = gather_facts(session, athlete_id, as_of)
    if facts.plan is None:
        raise LLMError("No hay plan (falta FTP: corre `cc estimate-cp`).")
    text = llm.complete(EXPLAIN_SYSTEM, explain_user(facts.to_prompt(), None))
    return Reply(text=text.strip(), intent=Intent(kind="plan"), facts=facts)


@dataclass
class ChatSession:
    """Conversación multivuelta con Vikon. Mantiene el historial y una intención
    PEGAJOSA: los minutos y la disposición persisten hasta que los cambies (así
    "40 min" seguido de "¿por qué?" sigue hablando del plan de 40 min). La ficha
    se recalcula cada vuelta con el estado vigente — el motor siempre decide.

    No retiene la sesión de BD (se pasa por turno) para servir también a la web,
    donde cada petición usa una conexión fresca."""

    athlete_id: int
    as_of: date
    llm: LLMClient
    minutes: float | None = None
    readiness: str | None = None
    history: list[dict[str, str]] = field(default_factory=list)

    def turn(self, session: Session, message: str) -> Reply:
        intent = parse_intent(self.llm, message)
        if intent.minutes is not None:
            self.minutes = intent.minutes
        if intent.readiness is not None:
            self.readiness = intent.readiness
        cri_override = _READINESS_CRI.get(self.readiness or "")

        facts = gather_facts(
            session, self.athlete_id, self.as_of,
            minutes=self.minutes, cri_override=cri_override,
        )
        system = f"{EXPLAIN_SYSTEM}\n\nFICHA ACTUAL (única fuente de cifras):\n{facts.to_prompt()}"
        messages = [
            {"role": "system", "content": system},
            *self.history,
            {"role": "user", "content": message},
        ]
        text = self.llm.chat(messages).strip()

        self.history.append({"role": "user", "content": message})
        self.history.append({"role": "assistant", "content": text})
        self.history = self.history[-12:]        # acota el contexto (6 turnos)
        return Reply(
            text=text,
            intent=Intent(
                kind=intent.kind, minutes=self.minutes,
                readiness=self.readiness, question=intent.question,
            ),
            facts=facts,
        )
