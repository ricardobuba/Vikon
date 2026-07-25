# AI Cycling Coach

Entrenador de ciclismo con IA de tipo **grey-box**: un motor fisiológico y un
planificador determinista toman las decisiones; el LLM explica y traduce, nunca
decide. Ver el diseño completo en
[`AI_Cycling_Coach_System_Design.md`](AI_Cycling_Coach_System_Design.md).

> **Estado: Fase 1 — Ingesta de datos.**
> Adaptador Strava → modelo canónico → Postgres → gemelo digital v0 (static + daily).

## Arquitectura de la Fase 1

```
Strava API ──▶ adapters/strava ──▶ modelo canónico ──▶ Postgres ──▶ gemelo v0
              (OAuth, cliente,      (domain/models)     (db/models)   (twin/)
               mapper)
```

- `domain/` — modelo de datos canónico (agnóstico de proveedor) + `AthleteState`.
- `adapters/` — un `ActivitySource` por proveedor; hoy, Strava.
- `db/` — ORM SQLAlchemy 2.0 + upserts idempotentes.
- `ingest.py` — backfill/sync que recorre un `ActivitySource` y persiste.
- `twin/` — construcción del estado v0 del gemelo (static + daily).
- `cli.py` — comandos: `db-create`, `strava-auth`, `backfill`, `twin-show`, `stats`.

## Requisitos previos (a instalar una sola vez)

1. **Docker Desktop** — https://www.docker.com/products/docker-desktop/
   (en Windows necesita WSL2; el instalador lo activa).
2. **uv** — gestor de paquetes Python: `pip install uv` (o el instalador de Astral).
3. **Una app de Strava** — https://www.strava.com/settings/api
   - *Authorization Callback Domain* = `localhost`
   - Copia el **Client ID** y el **Client Secret**.

## Puesta en marcha

```bash
# 1) Configuración
cp .env.example .env            # y rellena STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET

# 2) Base de datos (Postgres 16 + pgvector) en Docker
docker compose up -d

# 3) Dependencias del proyecto
uv sync

# 4) Esquema (bootstrap de desarrollo)
uv run cc db-create
#    …o con migraciones reales:
#    uv run alembic revision --autogenerate -m "initial"
#    uv run alembic upgrade head

# 5) Autorizar Strava (abre el navegador, captura el code en localhost)
uv run cc strava-auth

# 6) Importar tu histórico
uv run cc backfill --since 2015-01-01

# 7) Ver el gemelo y las estadísticas
uv run cc twin-show
uv run cc stats
```

## Desarrollo

```bash
uv run pytest        # tests (el del mapper no necesita BD ni red)
uv run ruff check .  # lint
uv run mypy src      # tipos
```

## Notas de diseño

- **Idempotencia**: `backfill` puede reejecutarse; hace upsert por
  `(provider, provider_activity_id)` y no vuelve a pedir streams ya guardados.
- **Trazabilidad**: cada actividad guarda su JSON crudo (`raw`) para reprocesar
  sin volver a llamar a la API.
- **pgvector** queda habilitado desde el inicio para el RAG científico (fases
  posteriores), pero aún no se usa.
- El **esquema del gemelo** (`AthleteState`) ya reserva las capas `slow` y
  `latent` que poblará la Fase 2, para no rehacerlo.
