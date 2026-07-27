"""Ficha de hechos: reúne el estado del gemelo y el plan en un bloque compacto.

Es la ÚNICA fuente de cifras para el LLM (anti-alucinación): si un dato no está
aquí, el asistente no puede afirmarlo. Se construye con el motor determinista.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from cycling_coach.db.repositories import (
    latest_parameter_estimate,
    next_goal,
    training_seconds_on,
)
from cycling_coach.planner.planner import (
    PlannedSession,
    phase_for,
    plan_session,
    roll_horizon,
)
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
    subjective_cri: float | None = None      # disposición que dijiste (usada en el plan)
    goal_date: date | None = None
    goal_name: str | None = None
    days_to_event: int | None = None
    phase: str | None = None
    plan: PlannedSession | None = None
    trained_today: bool = False          # ¿ya entrenó hoy?
    trained_minutes: int = 0
    plan_date: date | None = None        # día que planifica el plan (hoy o mañana)
    horizon: list[dict] = field(default_factory=list)   # próximos días (proyección)

    def to_prompt(self) -> str:
        """Serializa la ficha para el prompt (texto plano, claro y acotado)."""
        L: list[str] = [f"Fecha: {self.as_of.isoformat()}"]
        if self.trained_today:
            L.append(
                f"YA HA ENTRENADO HOY ({self.trained_minutes} min). El plan de abajo "
                f"es para MAÑANA ({self.plan_date.isoformat() if self.plan_date else '—'})."
            )
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
            L.append(f"Disposición calculada (CRI): {self.cri:.0f}/100{cov}")
        if self.subjective_cri is not None:
            L.append(
                f"Disposición que dijiste hoy y que USÓ el plan: "
                f"{self.subjective_cri:.0f}/100"
            )
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
        if self.horizon:
            L.append(
                "Plan proyectado de los próximos días (solo hoy se compromete; el "
                "resto se re-planifica con datos nuevos):"
            )
            for h in self.horizon:
                L.append(
                    f"  {h['day']}: {h['objective']} — {h['session']} "
                    f"({h['minutes']} min, ~{h['tss']} TSS)"
                )
        return "\n".join(L)


def planning_date(session: Session, athlete_id: int, today: date) -> tuple[date, int]:
    """Fecha que debe planificar el motor y minutos ya entrenados hoy. Si ya
    entrenó ≥20 min, planifica MAÑANA (la sesión de hoy está hecha)."""
    mins = training_seconds_on(session, athlete_id, today) // 60
    return (today + timedelta(days=1) if mins >= 20 else today), mins


def gather_facts(
    session: Session,
    athlete_id: int,
    as_of: date,
    *,
    minutes: float | None = None,
    cri_override: float | None = None,
    with_horizon: bool = False,
) -> Facts:
    """Construye la ficha. `minutes`/`cri_override` (si vienen del intent) se
    pasan al planificador determinista antes de calcular el plan. `with_horizon`
    proyecta la semana (para el chat); off en la pantalla Hoy (ahorra cálculo)."""
    facts = Facts(as_of=as_of)
    facts.ftp = latest_parameter_estimate(session, athlete_id, "ftp")
    facts.cp = latest_parameter_estimate(session, athlete_id, "cp")
    facts.w_prime = latest_parameter_estimate(session, athlete_id, "w_prime")

    # Si ya ha entrenado hoy (≥20 min), la sesión de hoy está hecha → el plan
    # es para MAÑANA (su estado matinal ya incluirá la carga de hoy).
    plan_date, facts.trained_minutes = planning_date(session, athlete_id, as_of)
    facts.trained_today = plan_date != as_of
    facts.plan_date = plan_date

    # UNA sola pasada de contexto (el suavizador de CP va cacheado). Antes se
    # recomputaba aquí y otra vez dentro de plan_today → ~2x más lento.
    current, ctx = build_training_context(session, athlete_id, plan_date)
    if current is not None:
        facts.tsb, facts.ctl, facts.atl = current.tsb, current.ctl, current.atl

    cri_detail = compute_cri_service(session, athlete_id, plan_date)
    if cri_detail is not None:
        facts.cri = cri_detail.result.cri
        facts.cri_coverage = cri_detail.result.coverage
        facts.cri_components = dict(cri_detail.result.components)
    facts.subjective_cri = cri_override      # None salvo que dijeras cómo te sientes

    goal = next_goal(session, athlete_id, plan_date)
    if goal is not None:
        facts.goal_date = goal.event_date
        facts.goal_name = goal.name or goal.kind
        facts.days_to_event = (goal.event_date - plan_date).days
        facts.phase = phase_for(facts.days_to_event).value

    if facts.ftp and current is not None:
        cri = cri_override if cri_override is not None else facts.cri
        facts.plan = plan_session(
            ftp=facts.ftp, tsb=current.tsb, ctl=current.ctl, atl=current.atl,
            cri=cri, context=ctx, minutes=minutes,
            phase=phase_for(facts.days_to_event), days_to_event=facts.days_to_event,
        )
        # Horizonte de la semana para que el chat conozca el plan futuro.
        facts.horizon = [] if not with_horizon else [
            {
                "day": h.day.isoformat(),
                "objective": h.plan.objective.value,
                "session": h.plan.template.name,
                "minutes": round(h.plan.template.total_minutes()),
                "tss": round(h.tss),
            }
            for h in roll_horizon(
                ftp=facts.ftp, ctl=current.ctl, atl=current.atl, context=ctx,
                cri=cri, days=7, start=plan_date,
                days_to_event=facts.days_to_event, minutes=minutes,
            )
        ]
    return facts
