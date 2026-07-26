"""Capa conversacional (cap. 11): el LLM EXPLICA y TRADUCE, nunca decide.

El motor + el planificador deciden (determinista, explicable). El LLM solo:
1) traduce tu texto libre a inputs estructurados para el planificador, y
2) redacta la explicación anclado a una ficha de hechos que calcula el motor.
Nunca inventa cifras ni prescripciones — si no está en la ficha, lo dice.
"""

from cycling_coach.assistant.llm import LLMClient, LLMError

__all__ = ["LLMClient", "LLMError"]
