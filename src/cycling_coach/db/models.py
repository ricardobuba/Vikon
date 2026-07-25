"""Modelos ORM (SQLAlchemy 2.0) — esquema núcleo de la Fase 1.

Entidades del cap. 12.2 relevantes para ingesta:
`athlete`, `provider_account`, `activity`, `stream`, `daily_metric`.
Las entidades de planificación (`workout_template`, `plan`, `evidence_rule`...)
llegan en Fase 3 y se añaden como nuevas migraciones.

Nota de trazabilidad (cap. 12.1): cada actividad guarda su `raw` JSONB del
proveedor y `ingested_at`, para poder reprocesar sin volver a llamar a la API.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Athlete(Base):
    """Variables permanentes del ciclista (capa `static` del gemelo, cap. 3.2)."""

    __tablename__ = "athlete"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(Text)
    sex: Mapped[str | None] = mapped_column(String(1))          # 'M' | 'F'
    birthdate: Mapped[date | None] = mapped_column(Date)
    height_cm: Mapped[float | None] = mapped_column(Double)
    # nominal; el peso que varía día a día va a daily_metric
    weight_kg: Mapped[float | None] = mapped_column(Double)
    experience: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    accounts: Mapped[list[ProviderAccount]] = relationship(
        back_populates="athlete", cascade="all, delete-orphan"
    )
    activities: Mapped[list[Activity]] = relationship(
        back_populates="athlete", cascade="all, delete-orphan"
    )


class ProviderAccount(Base):
    """Credenciales OAuth por proveedor. Los tokens se refrescan in situ."""

    __tablename__ = "provider_account"
    __table_args__ = (
        UniqueConstraint("provider", "provider_athlete_id", name="uq_provider_identity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    athlete_id: Mapped[int] = mapped_column(
        ForeignKey("athlete.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(Text)                 # "strava"
    provider_athlete_id: Mapped[str] = mapped_column(Text)
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scope: Mapped[str | None] = mapped_column(Text)

    athlete: Mapped[Athlete] = relationship(back_populates="accounts")


class Activity(Base):
    __tablename__ = "activity"
    __table_args__ = (
        UniqueConstraint("provider", "provider_activity_id", name="uq_provider_activity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    athlete_id: Mapped[int] = mapped_column(
        ForeignKey("athlete.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(Text)
    provider_activity_id: Mapped[str] = mapped_column(Text)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sport: Mapped[str] = mapped_column(Text)

    name: Mapped[str | None] = mapped_column(Text)
    elapsed_time_s: Mapped[int | None] = mapped_column(Integer)
    moving_time_s: Mapped[int | None] = mapped_column(Integer)
    distance_m: Mapped[float | None] = mapped_column(Double)
    elevation_gain_m: Mapped[float | None] = mapped_column(Double)
    avg_power_w: Mapped[float | None] = mapped_column(Double)
    weighted_avg_power_w: Mapped[float | None] = mapped_column(Double)
    max_power_w: Mapped[float | None] = mapped_column(Double)
    avg_hr: Mapped[float | None] = mapped_column(Double)
    max_hr: Mapped[float | None] = mapped_column(Double)
    avg_cadence: Mapped[float | None] = mapped_column(Double)
    avg_speed_mps: Mapped[float | None] = mapped_column(Double)
    kilojoules: Mapped[float | None] = mapped_column(Double)
    device_watts: Mapped[bool | None] = mapped_column(Boolean)
    trainer: Mapped[bool | None] = mapped_column(Boolean)

    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    athlete: Mapped[Athlete] = relationship(back_populates="activities")
    streams: Mapped[list[Stream]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )


class Stream(Base):
    """Serie temporal de un canal (watts, hr...) de una actividad.

    Fase 1: se almacena como JSONB (compacto y suficiente para 1 usuario). Si
    la escala lo pide, migrar a float8[] particionado / TimescaleDB (cap. 12.1).
    """

    __tablename__ = "stream"
    __table_args__ = (
        UniqueConstraint("activity_id", "stream_type", name="uq_activity_stream"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activity.id", ondelete="CASCADE"), index=True
    )
    stream_type: Mapped[str] = mapped_column(Text)
    data: Mapped[list] = mapped_column(JSONB)
    n_samples: Mapped[int] = mapped_column(Integer)

    activity: Mapped[Activity] = relationship(back_populates="streams")


class ParameterEstimate(Base):
    """Posterior de un parámetro `slow` del gemelo (CP, W', FTP...) en un instante.

    Append-only: cada re-estimación añade filas nuevas → se conserva el histórico
    de posteriores para auditar "qué sabíamos el día que decidimos algo" (cap. 12).
    """

    __tablename__ = "parameter_estimate"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    athlete_id: Mapped[int] = mapped_column(
        ForeignKey("athlete.id", ondelete="CASCADE"), index=True
    )
    param: Mapped[str] = mapped_column(Text)          # "cp" | "w_prime" | "ftp"
    mean: Mapped[float] = mapped_column(Double)
    sd: Mapped[float] = mapped_column(Double)
    ci90_low: Mapped[float] = mapped_column(Double)
    ci90_high: Mapped[float] = mapped_column(Double)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(Text)         # "prior"|"test"|"learned"|"import"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DailyMetric(Base):
    """Métrica diaria (capa `daily` del gemelo): sueño, HRV, FC reposo, CTL..."""

    __tablename__ = "daily_metric"
    __table_args__ = (
        UniqueConstraint("athlete_id", "day", "metric", name="uq_athlete_day_metric"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    athlete_id: Mapped[int] = mapped_column(
        ForeignKey("athlete.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[date] = mapped_column(Date, index=True)
    metric: Mapped[str] = mapped_column(Text)
    value: Mapped[float] = mapped_column(Double)
    source: Mapped[str] = mapped_column(Text)
