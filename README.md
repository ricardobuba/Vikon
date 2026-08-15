# Vikon

Entrenador de ciclismo de tipo **grey-box**: un motor fisiológico y un
planificador determinista toman las decisiones; el modelo de lenguaje explica y
traduce, **nunca decide**.

La diferencia con las apps del sector no es que use IA — es que **mide lo que
funciona y declara lo que no**. Cuando el modelo de carga solo explica el 14 %
de la variación del rendimiento, el README lo dice en vez de venderlo.

> ⚠️ **Vikon no es un producto sanitario.** Es una herramienta de planificación
> de entrenamiento deportivo, con fines informativos y de rendimiento. No
> diagnostica, no previene, no monitoriza ni trata ninguna enfermedad, y no
> sustituye el consejo de un profesional sanitario. El ejercicio intenso conlleva
> riesgo; consulta a tu médico antes de empezar. Ver
> [`FINALIDAD_PREVISTA.md`](FINALIDAD_PREVISTA.md).

---

## Qué hace

```
Strava ──▶ modelo canónico ──▶ CP / W′ / FTP ──▶ CTL / ATL / TSB ──▶ plan del día
           (agnóstico de       (filtro de        (carga)            + horizonte 7d
            proveedor)          Kalman)                                    │
                                                                           ▼
                                                              el LLM lo explica
```

- **Motor fisiológico** — potencia crítica y W′ con un filtro de Kalman en dos
  etapas, hiperparámetros aprendidos por máxima verosimilitud predictiva y
  validados con backtest *one-step-ahead* (cobertura de intervalos, NIS).
- **Planificador determinista** — el objetivo sale de tu forma; las restricciones
  de seguridad lo rebajan; la dosis la elige una simulación del modelo
  fitness-fatiga. El descanso **emerge** de la física, no de un calendario.
- **Capa conversacional** — el LLM traduce lenguaje libre a parámetros y redacta
  la explicación anclado a una ficha de hechos. Si un dato no está en la ficha,
  no puede afirmarlo.
- **Web mobile-first** — API JSON + frontend sin dependencias, instalable como PWA.

## Honestidad de método

Lo que hace distinto a este proyecto es lo que **no** afirma:

- El modelo de dosis-respuesta explica el **6,5 %** de la variación del CP
  (R² = 0,065). Bate al baseline, pero es débil, y está escrito así.
- El modelo de 3 parámetros se implementó, se midió, **empeoraba** la estimación
  del umbral, y se descartó. El código quedó documentado con el porqué.
- La durabilidad no es medible con los datos actuales: se declara, no se estima.
- El DFA-α1 se eliminó al confirmar que nunca habrá datos RR desde Strava.
- Los pesos del CRI se intentaron calibrar; no mejoraron; se mantuvieron los
  valores por defecto.

## Privacidad por diseño

- **No se almacena el GPS.** Strava envía `start_latlng`, `end_latlng` y la
  polilínea del recorrido en cada actividad; se descartan al importar. Con ellas
  se deduce el domicilio.
- **Retención automática** — los mensajes del chat viven 7 días; las series
  temporales, una ventana configurable. Ver [`retention.py`](src/cycling_coach/retention.py).
- **El LLM solo recibe métricas derivadas** — nunca datos crudos de Strava, ni
  nombres de actividad, ni coordenadas. Hay un test que falla si eso cambia:
  [`test_strava_ai_boundary.py`](tests/test_strava_ai_boundary.py).
- **Exportación y borrado completos** desde la propia app.

## Puesta en marcha

Necesitas [Docker](https://www.docker.com/products/docker-desktop/),
[uv](https://docs.astral.sh/uv/) y una app de Strava creada en
[strava.com/settings/api](https://www.strava.com/settings/api) con
*Authorization Callback Domain* = `localhost`.

```bash
cp .env.example .env     # rellena STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET
docker compose up -d     # Postgres 16 + pgvector
uv sync
uv run cc db-create
uv run cc strava-auth    # abre el navegador
uv run cc backfill --since 2015-01-01
uv run cc serve          # http://localhost:8730
```

Para el chat hace falta además una clave de LLM en el `.env`
(`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`). El cliente es agnóstico del
proveedor: cualquier endpoint compatible con OpenAI sirve — Gemini, Groq,
OpenRouter, Ollama en local o Anthropic.

## Comandos

```bash
uv run cc plan           # qué entrenar hoy, y por qué
uv run cc horizon        # los próximos 7 días
uv run cc chat           # conversación con Vikon
uv run cc estimate-cp    # recalcular CP / W′ / FTP
uv run cc coherence      # ¿cuadra el modelo con la curva real?
uv run cc backtest       # validación one-step-ahead
uv run cc retention      # aplicar la política de retención
uv run cc --help
```

## Desarrollo

```bash
uv run pytest            # 179 tests, sin necesidad de BD ni red
uv run ruff check .
uv run mypy src
```

## Estructura

| Módulo | Qué contiene |
|---|---|
| `domain/` | Modelo canónico, agnóstico de proveedor |
| `adapters/` | Un `ActivitySource` por proveedor; hoy, Strava |
| `metrics/`, `physiology/` | NP, MMP, CP/W′, carga, fitness-fatiga, coherencia |
| `planner/` | Objetivo, restricciones, dosis por simulación, horizonte |
| `twin/` | Estado del gemelo digital |
| `assistant/` | Ficha de hechos, prompts y cliente LLM |
| `web/` | API FastAPI + frontend mobile-first |
| `retention.py` | Política de retención de datos |

## Documentación

- [`AI_Cycling_Coach_System_Design.md`](AI_Cycling_Coach_System_Design.md) — diseño completo
- [`FINALIDAD_PREVISTA.md`](FINALIDAD_PREVISTA.md) — qué es Vikon y qué no, y por qué no lleva marcado CE
- [`RAG_Plan_Implementacion.md`](RAG_Plan_Implementacion.md) — RAG científico (propuesta)
- [`DESPLIEGUE_Plan.md`](DESPLIEGUE_Plan.md) — de la LAN a un servicio

## Aviso sobre la API de Strava

Este proyecto usa la API de Strava, sujeta a su
[API Agreement](https://www.strava.com/legal/api) y su
[API Policy](https://www.strava.com/legal/api_policy). Si lo despliegas, revisa
esos términos: la política vigente desde junio de 2026 limita la retención de
datos de Strava y restringe su uso en aplicaciones de IA. Vikon no está
afiliado, patrocinado ni respaldado por Strava.

## Licencia

[Apache-2.0](LICENSE). Se distribuye **sin garantía de ningún tipo**; ver las
secciones 7 y 8 de la licencia.
