"""Ficha de hechos: reúne el estado del gemelo y el plan en un bloque compacto.

Es la ÚNICA fuente de cifras para el LLM (anti-alucinación): si un dato no está
aquí, el asistente no puede afirmarlo. Se construye con el motor determinista.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from cycling_coach.db.repositories import latest_parameter_estimate, next_goal
from cycling_coach.planner.planner import PlannedSession, phase_for
from cycling_coach.planner.service import plan_today
from cycling_coach.twin.cri_service import compute_cri_service
from cycling_coach.twin.load_service import build_training_context


@dataclass
class Facts:
    as_of: date
    ftp: float | None = None
    cp: float | None = None
    w_prime: float | None = None
    tsb: float | None = None
    ctl: float | None = None
    atl: float | None = None
    cri: float | None = None
    cri_coverage: float | None = None
    cri_components: dict[str, float] = field(default_factory=dict)
    goal_date: date | None = None
    goal_name: str | None = None
    days_to_event: int | None = None
    phase: str | None = None
    plan: PlannedSession | None = None

    def to_prompt(self) -> str:
        """Serializa la ficha para el prompt (texto plano, claro y acotado)."""
        L: list[str] = [f"Fecha: {self.as_of.isoformat()}"]
        if self.ftp is not None:
            L.append(f"FTP: {self.ftp:.0f} W")
        if self.cp is not None:
            L.append(f"CP: {self.cp:.0f} W")
        if self.w_prime is not None:
            L.append(f"W': {self.w_prime / 1000:.1f} kJ")
        if self.tsb is not None:
            L.append(f"Forma (TSB): {self.tsb:+.1f}")
        if self.ctl is not None:
            L.append(f"Fitness (CTL): {self.ctl:.0f}")
        if self.atl is not None:
            L.append(f"Fatiga (ATL): {self.atl:.0f}")
        if self.cri is not None:
            cov = f" (cobertura {self.cri_coverage:.0%})" if self.cri_coverage else ""
            L.append(f"Disposición (CRI): {self.cri:.0f}/100{cov}")
        if self.cri_components:
            comps = ", ".join(f"{k}={v:.2f}" for k, v in self.cri_components.items())
            L.append(f"Componentes CRI disponibles: {comps}")
        if self.goal_date is not None:
            name = self.goal_name or "evento"
            L.append(
                f"Meta: {name} el {self.goal_date.isoformat()} "
                f"(faltan {self.days_to_event} días, fase {self.phase})"
            )
        else:
            L.append("Meta: ninguna registrada")
        if self.plan is not None:
            p = self.plan
            L.append(
                f"Plan de hoy: objetivo={p.objective.value}; sesión={p.template.name} "
                f"({p.template.total_minutes():.0f} min)"
            )
            if p.targets:
                L.append("Bloques: " + " | ".join(p.targets))
            L.append(f"Razón del motor: {p.rationale}")
        return "\n".join(L)


def gather_facts(
    session: Session,
    athlete_id: int,
    as_of: date,
    *,
    minutes: float | None = None,
    cri_override: float | None = None,
) -> Facts:
    """Construye la ficha. `minutes`/`cri_override` (si vienen del intent) se
    pasan al planificador determinista antes de calcular el plan."""
    facts = Facts(as_of=as_of)
    facts.ftp = latest_parameter_estimate(session, athlete_id, "ftp")
    facts.cp = latest_parameter_estimate(session, athlete_id, "cp")
    facts.w_prime = latest_parameter_estimate(session, athlete_id, "w_prime")

    current, _ = build_training_context(session, athlete_id, as_of)
    if current is not None:
        facts.tsb, facts.ctl, facts.atl = current.tsb, current.ctl, current.atl

    cri_detail = compute_cri_service(session, athlete_id, as_of)
    if cri_detail is not None:
        facts.cri = cri_detail.result.cri
        facts.cri_coverage = cri_detail.result.coverage
        facts.cri_components = dict(cri_detail.result.components)

    goal = next_goal(session, athlete_id, as_of)
    if goal is not None:
        facts.goal_date = goal.event_date
        facts.goal_name = goal.name or goal.kind
        facts.days_to_event = (goal.event_date - as_of).days
        facts.phase = phase_for(facts.days_to_event).value

    facts.plan = plan_today(
        session, athlete_id, as_of, minutes=minutes, cri_override=cri_override
    )
    return facts
