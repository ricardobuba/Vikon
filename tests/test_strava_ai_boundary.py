"""Frontera Strava↔IA: `assistant/` (todo lo que habla con el LLM) nunca debe
poder tocar datos CRUDOS de Strava (`Activity.raw`, `Stream.data`, o el
resumen de `twin/activity_service.py`), solo datos ya derivados por el motor
(parameter_estimate, daily_metric, activity_mmp, CTL/TSB/CRI...).

Esto no es un capricho de estilo: la API Policy de Strava (§5.3) prohíbe usar
Strava Data "directa o indirectamente" en la operación de cualquier AI
Application. La defensa es un firewall demostrable en código — ver
BLINDAJE_LEGAL_Plan.md §2. Si este test falla, alguien ha importado datos
crudos dentro de `assistant/` y hay que sacarlos antes de mergear.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import cycling_coach.assistant as assistant_pkg
from cycling_coach.assistant.grounding import Facts

ASSISTANT_DIR = Path(assistant_pkg.__file__).parent

# Módulos que dan acceso a datos crudos de Strava si se importan enteros.
FORBIDDEN_MODULES = {
    "cycling_coach.twin.activity_service",   # resumen de actividades: lee Activity.raw
    "cycling_coach.db.models",                # accedería a Activity.raw / Stream.data
    "cycling_coach.ingest",                   # escribe los crudos al ingerir
}
# Símbolos concretos prohibidos si algún día se importan con nombre desde un
# módulo que sí es legítimo tocar por otra razón.
FORBIDDEN_NAMES = {"Activity", "Stream"}


def _imports_in(path: Path) -> list[tuple[str, str]]:
    """(módulo, nombre) de cada import del fichero ('*' si es `import módulo`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                found.append((node.module, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, "*"))
    return found


def test_assistant_never_imports_raw_strava_symbols():
    offenders = []
    for path in ASSISTANT_DIR.glob("*.py"):
        for module, name in _imports_in(path):
            if module in FORBIDDEN_MODULES:
                offenders.append(f"{path.name}: from {module} import {name}")
            if name in FORBIDDEN_NAMES:
                offenders.append(f"{path.name}: import de simbolo '{name}' ({module})")
    assert not offenders, (
        "assistant/ ha importado datos CRUDOS de Strava (Activity/Stream/"
        "activity_service/ingest) -> rompe la frontera Strava<->IA documentada "
        "en BLINDAJE_LEGAL_Plan.md #2:\n" + "\n".join(offenders)
    )


# Campos que, si aparecieran en Facts, indicarian que se ha colado un dato
# crudo de Strava (texto libre de actividad, GPS, streams) en la ficha que
# se serializa al prompt del LLM.
FORBIDDEN_FACT_FIELDS = {
    "raw", "activity_name", "name", "gps", "latlng", "polyline",
    "stream", "streams", "activities",
}


def test_facts_schema_has_no_raw_strava_fields():
    field_names = {f.name for f in dataclasses.fields(Facts)}
    leaked = field_names & FORBIDDEN_FACT_FIELDS
    assert not leaked, f"Facts expone campos que huelen a dato crudo de Strava: {leaked}"


def test_facts_prompt_only_contains_derived_numbers():
    """Ficha con todos los campos rellenos -> el texto que ve el LLM no debe
    contener nada que solo pueda venir de Activity.raw/Stream.data (no hay
    forma de "colar" eso sin tocar Facts, pero blindamos el contrato: el
    prompt se construye SOLO desde los campos del dataclass, sin leer BD)."""
    from datetime import date

    from cycling_coach.planner.library import Objective, WorkoutTemplate
    from cycling_coach.planner.planner import PlannedSession

    template = WorkoutTemplate(
        id="ss_test", objective=Objective.sweet_spot, name="sweet_spot",
        blocks=[], description="test",
    )
    plan = PlannedSession(
        objective=Objective.sweet_spot, template=template, ftp=348.0,
        targets=["3x12' @ 306-324W"], rationale="TSB bajo, toca calidad",
    )
    facts = Facts(
        as_of=date(2026, 8, 13), ftp=348.0, cp=346.0, w_prime=21000,
        tsb=-17.8, ctl=47.0, atl=64.0, cri=73.0, cri_coverage=0.75,
        plan=plan,
    )
    block = facts.to_prompt()
    # El bloque se construye por concatenacion de f-strings sobre los campos
    # del dataclass: no hay ninguna consulta a BD ni a Activity/Stream aqui.
    assert "FTP: 348" in block and "TSB): -17.8" in block
