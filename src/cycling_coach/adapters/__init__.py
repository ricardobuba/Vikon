"""Capa adaptadora: cada proveedor implementa `ActivitySource` y normaliza
sus datos al modelo canónico (`cycling_coach.domain`)."""

from cycling_coach.adapters.base import ActivitySource

__all__ = ["ActivitySource"]
