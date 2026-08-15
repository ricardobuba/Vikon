"""El bozal sanitario del LLM está puesto (FINALIDAD_PREVISTA.md).

No se puede testear lo que un LLM responderá — es probabilístico y vive al otro
lado de la red. Lo que SÍ se puede testear, y es lo que importa, es que la
instrucción esté en el prompt de sistema que sale por el cable en cada turno.

Por qué importa: bajo el MDR (art. 2(12)) la "finalidad prevista" de un
producto se deduce también de lo que el software DICE en pantalla. Un LLM sin
bozal puede convertir en producto sanitario un motor que no lo es — con
marcado CE, organismo notificado y evaluación clínica detrás. Es la línea más
fácil de cruzar sin darse cuenta, y no se cruza por código sino por texto.
"""

from __future__ import annotations

from cycling_coach.assistant.prompts import EXPLAIN_SYSTEM

# Cada entrada es (fragmento que debe aparecer, por qué está ahí).
PROHIBICIONES = [
    ("Diagnosticar", "MDCG 2019-11 §3.2: diagnosticar es finalidad médica"),
    ("interpretar síntomas", "interpretar síntomas es acto sanitario"),
    ("problema de salud", "convertir un dato en alarma de salud = MDSW"),
    ("riesgo cardiovascular", "MDCG 2019-11 §6.1, ejemplo textual"),
    ("arritmias", "MDCG 2019-11 §3.2 Nota 1, ejemplo textual"),
    ("rehabilitación", "rehabilitación es finalidad médica explícita"),
    ("profesional sanitario", "tiene que existir la vía de derivación"),
]


def test_el_prompt_lleva_el_bozal_sanitario():
    faltan = [
        f"{frag!r} ({motivo})"
        for frag, motivo in PROHIBICIONES
        if frag.lower() not in EXPLAIN_SYSTEM.lower()
    ]
    assert not faltan, (
        "El prompt de sistema ha perdido parte del límite sanitario. Sin él, lo "
        "que el LLM escriba puede redefinir la finalidad prevista de Vikon y "
        "arrastrarlo al régimen de producto sanitario. Falta:\n  "
        + "\n  ".join(faltan)
    )


def test_el_bozal_prohibe_los_verbos_regulados():
    """'Prevenir', 'diagnosticar' y 'tratar' son verbos del art. 2(1) MDR: usarlos
    referidos a la salud es lo que define un producto sanitario."""
    bloque = EXPLAIN_SYSTEM.lower()
    assert "prevenir" in bloque and "diagnosticar" in bloque and "tratar" in bloque
    # Y deben aparecer como PROHIBICIÓN, no como capacidad ofrecida.
    assert "nunca" in bloque


def test_el_prompt_manda_tratar_lo_fisiologico_como_entrenamiento():
    assert "clave de entrenamiento" in EXPLAIN_SYSTEM
