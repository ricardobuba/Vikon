# Plan de implementación — RAG científico (Vikon)

> Extiende el §10 del *System Design Document* ("Motor científico: RAG + reglas")
> con el detalle de ejecución. Estado: **propuesta**, sin implementar.

---

## 1. Objetivo y la línea roja

El RAG debe hacer que la prescripción sea **específica y defendible**: que un
criterium entrene como un criterium *porque la literatura lo dice*, y no porque
alguien colocó unas constantes a ojo. Hoy `planner.py` tiene
`_EVENT_QUALITY_MENU` marcado **PROVISIONAL** justo por esto, y constantes como
`DELOAD_FRACTION = 0.6` o `QUALITY_SPACING_DAYS = 4` sin respaldo explícito.

**La línea roja (principio grey-box): el RAG no decide entrenamientos.**

Si el LLM lee papers y responde "para tu criterium haz 4×4 min", se pierde todo
lo que hace fiable a Vikon: determinismo, tests, reproducibilidad y trazabilidad.
El RAG aporta **parámetros y rangos** al motor, y **citas** al explicador. El
motor sigue eligiendo el número.

---

## 2. Por qué el RAG naíf falla en este dominio

Conviene decirlo antes de construir, porque marca el diseño:

| Fallo | Consecuencia aquí |
|---|---|
| Recupera lo semánticamente parecido, no lo evidencialmente fuerte | Un estudio sin control de 12 sujetos "pesa" igual que un metaanálisis |
| Promedia contradicciones | La literatura de intervalos se contradice de verdad; la media es mentira |
| Ignora la población | Casi todo está hecho en no entrenados o moderadamente entrenados. Tú estás a ~350 W de FTP: mucho no transfiere |
| Suena autoritario | El error plausible con cita es **más** peligroso que el error evidente |
| No es testeable | Adiós a las 151 pruebas actuales como red de seguridad |

Ninguno se arregla con "mejores embeddings". Se arreglan con arquitectura.

---

## 3. Arquitectura: dos capas separadas

### Capa 1 — Evidencia → Parámetros (offline, curada, determinista)

Extracción de prescripciones **cuantitativas** a una tabla estructurada,
revisada y versionada. Es lo único que lee el planificador.

```
paper → chunks → claims candidatos (LLM propone) → REVISIÓN HUMANA → claims aprobados → parámetros
```

Ejemplo de claim aprobado:

```json
{
  "intervention": "vo2max_intervals",
  "parameter": "work_duration_s",
  "value_range": [180, 300],
  "population": {"status": "trained", "vo2max_ml_kg_min": [60, 72], "n": 21},
  "design": "rct_crossover",
  "evidence_tier": 2,
  "effect": {"metric": "vo2max", "direction": "+", "reported": "3.4%"},
  "quote": "<cita literal del paper>",
  "doi": "10.xxxx/xxxxx"
}
```

### Capa 2 — Recuperación para explicar (online)

Cuando el coach explica *por qué* la sesión de hoy, recupera los pasajes que la
sostienen y **los cita**. El LLM no inventa la prescripción: la traduce.

**Esta separación es la respuesta a "¿cómo uso un LLM sobre papers sin romper el
grey-box?"** La capa 1 es auditable y testeable; la capa 2 es solo lenguaje.

---

## 4. Modelo de datos (pgvector, dentro del Postgres actual)

Cero infraestructura nueva — ya está decidido en el §diseño y en el presupuesto.

```sql
paper(id, doi UNIQUE, title, journal, year, authors, abstract,
      oa_status, license, url, evidence_tier, population_json, ingested_at)

chunk(id, paper_id, section, ord, text, n_tokens,
      embedding vector(1024), tsv tsvector)          -- híbrido: denso + léxico

claim(id, paper_id, intervention, parameter, value_low, value_high, units,
      population_json, design, evidence_tier, effect_json,
      quote, quote_verified bool,
      status ENUM('pendiente','aprobado','rechazado'),
      reviewed_by, reviewed_at, notes)

claim_conflict(claim_a, claim_b, kind)               -- contradicciones detectadas
```

**Decisiones concretas:**

- **Chunking por secciones**, no por tamaño fijo. Métodos, Resultados y Discusión
  no valen lo mismo, y una tabla debe viajar con su leyenda. ~400–800 tokens,
  15% de solape.
- **Recuperación híbrida**: coseno pgvector **+** full-text de Postgres,
  fusionados con *Reciprocal Rank Fusion*. Imprescindible: la búsqueda densa
  falla con términos exactos como `W′`, `4×4 min` o `85% VO2max`.
- **Embeddings vía API** (sin GPU, céntimos). Corpus en inglés, consultas
  traducidas al inglés antes de embeber — más barato y mejor que un modelo
  multilingüe.
- **Reranking por evidencia**, no solo por similitud:

  ```
  score = similitud × w_tier × w_población × w_recencia
  ```

  El emparejamiento de población es lo que da la especificidad real que pides.

---

## 5. Fases

### Fase 0 — Corpus y legalidad *(decisión tuya)*

Fuentes limpias y gratuitas:

- **Europe PMC REST API** — abstracts + texto completo del subconjunto OA
- **PubMed E-utilities** — abstracts
- **PLOS, Frontiers, MDPI, SpringerOpen, DOAJ** — OA nativo
- **bioRxiv / medRxiv** — preprints (tier bajo, marcados como tal)

**Límite honesto:** la mayoría de *Med Sci Sports Exerc*, *Int J Sports Physiol
Perform* y buena parte de *Sports Medicine* está de pago. No voy a construir un
scraper de contenido cerrado. Para papers a los que tengas acceso legítimo,
ingerirlos para **tu uso personal** es defendible; redistribuirlos en un producto
no lo es. Esa frontera la decides tú y conviene dejarla escrita.

Consultas semilla (a validar por ti): distribución de intensidad polarizada,
prescripción de intervalos VO2máx, demandas fisiológicas del criterium,
periodización en bloques, tapering, sweet spot / umbral, capacidad de sprints
repetidos, deload.

**Volumen objetivo: 300–500 papers bien elegidos, no 50.000.** Aquí la curación
gana a la cantidad, y de largo.

### Fase 1 — Ingesta y almacenamiento *(mío)*

Cliente Europe PMC/PubMed, parseo de secciones, chunking, embeddings, migración
pgvector, índices (`ivfflat` sobre el vector, GIN sobre el tsvector).

### Fase 2 — Gradación de evidencia *(mío, criterios tuyos)*

Clasificación por diseño (metaanálisis ▶ revisión sistemática ▶ RCT ▶ cruzado ▶
cohorte ▶ serie de casos ▶ narrativa ▶ opinión), extracción de `n`, nivel de
entrenamiento de los sujetos, duración y tamaño del efecto.

**Detección de contradicciones:** agrupar claims sobre la misma pregunta; si las
direcciones del efecto discrepan, **mostrar ambas con su tier**. Nunca promediar
un desacuerdo real — es más honesto y además más útil.

### Fase 3 — Extracción de claims *(pipeline mío, aprobación tuya)*

El LLM propone claims estructurados con **cita literal obligatoria**. Si no puede
citar textualmente, se rechaza automáticamente. Es el mismo patrón
anti-alucinación que ya usa `assistant.py` con `_LOG_RANGE` y `_PROFILE_RANGE`:
el LLM traduce, los rangos filtran.

Verificación programática: la cita debe existir *verbatim* en el chunk de origen
(`quote_verified`). Barato y elimina la clase entera de invenciones.

**Cola de revisión en la app**: apruebas / editas / rechazas. Solo lo aprobado
influye en el planificador. Es la puerta del grey-box, y es tu papel irreducible.

### Fase 4 — Cableado al planificador *(mío)*

Aquí muere el `PROVISIONAL`:

- `_EVENT_QUALITY_MENU` pasa a **derivarse** de claims aprobados sobre las
  demandas del evento. Tu requisito — *un criterium necesita resistencia y FTP
  también* — sale solo, porque es lo que dice la literatura, no porque yo
  equilibrase el menú a mano.
- Constantes que pasan a tener respaldo y rango: `DELOAD_FRACTION`,
  `QUALITY_SPACING_DAYS`, `DELOAD_AFTER_BUILD_WEEKS`, duraciones y work:rest de
  intervalos, distribución polarizada/piramidal por fase, duración y recorte de
  volumen del taper.
- **Los claims dan rangos; el motor sigue eligiendo el número.** Todo detrás de
  un *feature flag* para poder comparar A/B contra el motor actual.

### Fase 5 — Explicación con citas *(mío)*

`grounding.py` gana un bloque `citations` en la ficha de hechos. El prompt ya
prohíbe inventar cifras; se añade "cita solo desde las referencias dadas, por
[n]". En la UI, el "Por qué" del detalle del día gana notas al pie con
título/DOI. Verificable, que es justo el objetivo.

### Fase 6 — Evaluación *(mía la infra, tuyo el criterio)*

Sin esto no se sabe si funciona:

- **Conjunto dorado**: ~50 preguntas con respuesta que tú validas.
- **Métricas**: recall@k de recuperación; fidelidad de citas (comprobable
  automáticamente); precisión de extracción medida contra tus decisiones de
  revisión.
- **Regresión del planificador**: las 151 pruebas actuales deben seguir pasando,
  más pruebas nuevas que fijen los parámetros derivados en rangos sanos.
- **Canario**: una prueba que falle si aparece un DOI inexistente.

---

## 6. Recomendación de vídeos

Aquí el RAG aporta más de lo que parece, pero no donde uno espera. Tres usos, de
menor a mayor valor:

### (A) Vídeos para seguir la sesión (indoor)

El emparejamiento básico —"hoy toca 6×3 min al 110%, busca un vídeo así"— **no
necesita RAG**: necesita un índice estructurado de vídeos (duración, intervalos,
zonas objetivo).

Donde sí entra el RAG es en **qué sustitución es aceptable**. ¿Vale un 5×4 min al
105% en lugar de 6×3 min al 115%? Eso lo responde la tabla de claims: los rangos
de duración e intensidad que producen la misma adaptación. Sin evidencia, un
recomendador solo puede hacer coincidencia difusa; con ella hace **equivalencia
justificada**.

### (B) Vídeos educativos sobre la sesión de hoy

Hoy toca VO2máx → se recuperan los claims → los conceptos (potencia aerobia
máxima, tiempo a VO2máx, work:rest) → se recomiendan vídeos que cubran esos
conceptos.

Y algo que casi ningún recomendador puede hacer: **filtrar pseudociencia**.
Las afirmaciones de un vídeo se pueden contrastar con la tabla de evidencia.

### (C) Ranking por alineación con la evidencia *(el más valioso)*

Puntuar cada vídeo candidato según lo bien que su protocolo encaja con los rangos
respaldados para la adaptación que buscas. Así "recomendado" **significa algo**,
y puedes mostrar *por qué*, con la cita. Eso es un diferenciador real frente a
cualquier recomendador por popularidad.

### Límites honestos de esta parte

Es la pieza menos cierta del plan y hay que verificar términos antes de construir:

- **YouTube Data API v3**: cuota gratuita de 10.000 unidades/día, pero una
  búsqueda cuesta ~100 → ~100 búsquedas diarias. Suficiente para uso personal,
  no para indexar a lo bruto.
- **Los subtítulos son el problema.** La API solo permite descargarlos de vídeos
  propios; raspar transcripciones de terceros es zona gris de los términos de
  servicio. Diseñar sobre **título, descripción y capítulos** (sí disponibles),
  no sobre transcripciones.
- **Los títulos mienten.** "BRUTAL VO2 MAX WORKOUT" pueden ser 40 min de tempo.
  Hace falta extraer estructura con **puntuación de confianza** y marcar
  explícitamente "sin verificar" cuando no se puede.

**Ambigüedad que no resuelvo solo:** no sé si querías (A) vídeos para pedalear
siguiéndolos o (B) vídeos para aprender. El plan cubre ambos, pero la prioridad
la marcas tú.

---

## 7. Reparto de trabajo

### Tuyo (no delegable)

1. **Alcance del corpus** y validación de las consultas semilla — juicio de dominio.
2. **Decisión legal** sobre qué fuentes se ingieren y dónde está tu frontera.
3. **Claves de API** en `.env` (embeddings, YouTube). Como siempre, las manejas tú.
4. **Aprobar los claims extraídos.** Es la puerta del grey-box y el trabajo real:
   estima ~5–10 min/día durante dos semanas, o un fin de semana en lote.
5. **Definir el conjunto dorado**: qué es "correcto" para *tu* entrenamiento.
6. **Decidir cuándo la evidencia debe pisar tu intuición** — y cuándo no.

### Mío

Esquema y migraciones pgvector · clientes de ingesta · chunking por secciones ·
recuperación híbrida + RRF · heurísticas de gradación · pipeline de extracción con
verificación de citas literales · UI de revisión · cableado al planificador tras
feature flag · notas al pie con DOI · arnés de evaluación y tests · instrumentación
de coste.

---

## 8. Coste

| Partida | Coste |
|---|---|
| Europe PMC / PubMed | 0 € |
| Embeddings, 500 papers (~4M tokens) | < 1 € (una vez) |
| Extracción de claims, 500 papers | unos pocos € (una vez) |
| pgvector | 0 € (Postgres ya está) |
| Consultas en uso | despreciable |

**Puntual: unos pocos euros. Recurrente: ~0.** Cabe de sobra en el límite de
15 €/mes.

---

## 9. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| La maldición de lo plausible | Cita literal obligatoria + verificación programática + aprobación humana |
| La literatura no transfiere a tu nivel | Emparejamiento de población y penalización explícita, no ocultada |
| Deriva hacia "el LLM decide" | Feature flag + puerta de aprobación + tests de regresión |
| Contradicciones reales | Se muestran las dos posturas con su tier; no se promedian |
| Corpus sesgado a lo que es OA | Declararlo en la UI; el sesgo conocido es mejor que el oculto |

---

## 10. Recomendación de arranque

**No construir el RAG completo. Construir una rebanada vertical.**

**Hito 1**: 30 papers sobre **una** pregunta — demandas fisiológicas del
criterium + prescripción de intervalos VO2máx — recorriendo el camino entero:
ingesta → claims → aprobación → el planificador lo usa → aparece citado en la UI.

Si esa rebanada convence, se escala el corpus. Si no, lo hemos aprendido barato,
y esa es exactamente la razón de empezar así.
