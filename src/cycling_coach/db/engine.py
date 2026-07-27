"""Motor SQLAlchemy y gestión de sesiones."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from cycling_coach.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sesión transaccional: commit al salir sin error, rollback si lo hay."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Columnas añadidas a tablas ya existentes (create_all NO altera tablas). Todas
# idempotentes (ADD COLUMN IF NOT EXISTS). Para producción → migraciones Alembic.
_ADD_COLUMNS = [
    "ALTER TABLE activity ADD COLUMN IF NOT EXISTS "
    "is_maximal_test boolean NOT NULL DEFAULT false",
    "ALTER TABLE model_config ADD COLUMN IF NOT EXISTS cri_weights jsonb",
    "ALTER TABLE athlete ADD COLUMN IF NOT EXISTS level text",
    "ALTER TABLE athlete ADD COLUMN IF NOT EXISTS declared_ftp_w double precision",
    "ALTER TABLE athlete ADD COLUMN IF NOT EXISTS hr_max integer",
    "ALTER TABLE athlete ADD COLUMN IF NOT EXISTS hr_rest integer",
    "ALTER TABLE athlete ADD COLUMN IF NOT EXISTS weekly_minutes_target integer",
    "ALTER TABLE athlete ADD COLUMN IF NOT EXISTS onboarded boolean NOT NULL DEFAULT false",
]


def ensure_schema(engine: Engine | None = None) -> None:
    """Crea las tablas que falten y añade columnas nuevas a las existentes.
    Idempotente: seguro de llamar en cada arranque. No borra ni migra datos."""
    from sqlalchemy import text

    from cycling_coach.db.models import Base

    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _ADD_COLUMNS:
            conn.execute(text(stmt))
