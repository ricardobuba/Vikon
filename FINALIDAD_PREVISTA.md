# Finalidad prevista de Vikon

> Documento de una página, versionado en git. Es el texto contra el que se
> audita todo lo demás: la web, la ficha de la tienda, el README, las capturas,
> el prompt del asistente y cualquier cosa que se diga en público sobre Vikon.
>
> Existe por una razón concreta: bajo el Reglamento de Productos Sanitarios
> (MDR, UE 2017/745, art. 2(12)), la *finalidad prevista* de un producto no la
> fija su código — la fijan **las declaraciones del fabricante**, incluido el
> material promocional y lo que el software muestra en pantalla. Un motor que
> no es producto sanitario puede convertirse en uno por una frase de marketing.

---

## La frase

**Vikon es una herramienta de planificación de entrenamiento de ciclismo, con
fines informativos y de rendimiento deportivo.**

Esa frase debe aparecer idéntica en los términos de servicio, en el README y en
la descripción de la tienda. No es marketing: es la declaración de finalidad.

## Qué hace

Estima parámetros de rendimiento del propio deportista (potencia crítica, W′,
FTP) a partir de sus entrenamientos, calcula carga de entrenamiento
(CTL/ATL/TSB) y propone sesiones. Un modelo de lenguaje traduce lo que escribe
el usuario y **redacta** las explicaciones; **no decide** el entrenamiento —
eso lo hace un motor determinista y explicable.

## Qué NO hace, y no debe hacer nunca

No diagnostica, no previene, no monitoriza ni trata ninguna enfermedad. No
sustituye el consejo de un profesional sanitario. Su uso no crea una relación
médico-paciente.

## Población destinataria

Personas **adultas y sanas** que practican ciclismo por deporte o recreación.

**Excluidos expresamente**: menores de edad, personas embarazadas, personas con
cardiopatía, arritmia o cualquier patología diagnosticada, y **cualquier uso de
rehabilitación clínica o de vuelta al deporte tras lesión o cirugía**.

Esta exclusión no es cautela retórica: la rehabilitación es el ejemplo textual
de software sanitario en la guía MDCG 2019-11 rev.1 §3.2. Es la puerta por la
que Vikon se convertiría en producto sanitario sin tocar una línea de código.

## Por qué no lleva marcado CE

La guía **MDCG 2019-11 rev.1 §3.1** establece que el software destinado
únicamente a finalidades no médicas —como las aplicaciones de bienestar o
*fitness*— no califica como software sanitario (MDSW). Vikon se mantiene en ese
lado mientras respete las líneas de abajo.

---

## Las seis líneas que no se cruzan

Cada una es un ejemplo textual del MDCG 2019-11 rev.1 o del Anexo VIII del MDR.
Cruzar cualquiera implicaría clase IIa como mínimo: organismo notificado, ISO
13485, IEC 62304, evaluación clínica, marcado CE y vigilancia poscomercial.
Para un autor individual, eso equivale a cerrar el producto.

1. **No detectar latidos irregulares ni arritmias.** Si algún día se usan
   intervalos RR o DFA (están en el backlog), se presentan **solo** como carga
   y disponibilidad para entrenar, jamás como señal de ritmo cardiaco.
2. **No estimar riesgo cardiovascular, "edad cardiaca" ni riesgo metabólico** —
   aunque la fórmula sea trivial y el dato ya esté en el pipeline.
3. **No entrar en rehabilitación, vuelta tras lesión, alivio del dolor ni
   adaptación para cardiópatas.**
4. **No decir "previene".** Ni "previene lesiones", ni "detecta problemas antes
   de que aparezcan", ni "protege tu salud cardiovascular". Se reformula en
   clave de rendimiento: *evita que acumules más fatiga de la que puedes
   asimilar*.
5. **No presentar la frecuencia cardiaca como monitorización de una constante
   vital.** Panel de entrenamiento, no monitor de paciente.
6. **No generar alarmas fisiológicas.** *"Hoy toca descanso"* o *"tu FTP ha
   bajado un 4%"* es fitness. *"Tu FC en reposo está anormalmente alta,
   revísalo"* es la definición literal de software sanitario. La salida de
   cualquier detección de anomalías tiene que ser **una decisión de
   entrenamiento, nunca un aviso de salud**.

## Cómo se hace cumplir en el código

- `src/cycling_coach/assistant/prompts.py` — el bloque **LÍMITE SANITARIO** del
  prompt de sistema prohíbe explícitamente las seis líneas y define la
  respuesta de derivación al profesional sanitario.
- `tests/test_medical_boundary.py` — falla si ese bloque desaparece o se
  debilita. El LLM no se puede testear; el bozal sí.

## Relación con el Reglamento de IA

Vikon es un sistema de IA de **riesgo limitado**: el Anexo III del Reglamento
(UE) 2024/1689 no contiene ninguna categoría de salud, deporte o bienestar. La
única vía por la que pasaría a alto riesgo es convirtiéndose en producto
sanitario (Anexo I) — es decir, cruzando alguna de las seis líneas de arriba.

Le aplica el **art. 50** (transparencia: avisar de que se interactúa con una
IA), que es exigible desde el 2 de agosto de 2026 y **no** quedó aplazado por
el Reglamento (UE) 2026/1744.

---

*Última revisión: 2026-08-14. Si cambia lo que Vikon hace, este documento se
actualiza ANTES que el código.*
