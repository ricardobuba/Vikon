"""Motor de planificación (Fase 3): estado → objetivo → sesión + explicación."""

from cycling_coach.planner.planner import (
    FormThresholds,
    Phase,
    PlannedSession,
    RecentDay,
    TrainingContext,
    apply_constraints,
    apply_phase,
    choose_objective,
    phase_for,
    plan_session,
    render_targets,
)

__all__ = [
    "FormThresholds",
    "Phase",
    "PlannedSession",
    "RecentDay",
    "TrainingContext",
    "apply_constraints",
    "apply_phase",
    "choose_objective",
    "phase_for",
    "plan_session",
    "render_targets",
]
