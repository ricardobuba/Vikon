"""Capa web (FastAPI): API JSON del gemelo + frontend mobile-first.

La API expone lo que el motor ya calcula (estado, plan, horizonte, coherencia)
y la conversación. Es reutilizable: hoy la consume una web local; mañana, una
app móvil nativa. El frontend estático vive en `web/static/`.
"""

from cycling_coach.web.api import create_app

__all__ = ["create_app"]
