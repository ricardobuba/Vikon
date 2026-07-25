"""Gemelo digital: estado (static/daily) + estimación de parámetros slow (CP/W')."""

from cycling_coach.twin.builder import build_state
from cycling_coach.twin.cp_estimation import CPEstimationResult, estimate_cp

__all__ = ["CPEstimationResult", "build_state", "estimate_cp"]
