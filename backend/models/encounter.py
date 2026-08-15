"""Triage encounter and navigation action history database models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.model_run import JSONType

if TYPE_CHECKING:
    from backend.models.member import Member


class TriageEncounter(Base):
    """Persistent symptom triage encounter record."""

    __tablename__ = "triage_encounters"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True
    )
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chief_complaint: Mapped[str | None] = mapped_column(Text, nullable=True)
    symptoms_duration: Mapped[str | None] = mapped_column(String(128), nullable=True)
    associated_symptoms: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    selected_red_flags: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    has_red_flags: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_emergency: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recommended_acuity: Mapped[str] = mapped_column(String(64), nullable=False)
    urgency_level: Mapped[str] = mapped_column(String(64), nullable=False)
    recommended_setting_name: Mapped[str] = mapped_column(String(255), nullable=False)
    clinical_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    safety_disclaimer: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_rules: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, nullable=True)
    rule_set_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    member: Mapped[Member | None] = relationship()
    navigation_actions: Mapped[list[NavigationAction]] = relationship(
        back_populates="encounter", cascade="all, delete-orphan"
    )


class NavigationAction(Base):
    """Patient action / outcome record associated with a triage encounter or member."""

    __tablename__ = "navigation_actions"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    encounter_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("triage_encounters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selected_acuity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_details: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    encounter: Mapped[TriageEncounter | None] = relationship(back_populates="navigation_actions")
    member: Mapped[Member | None] = relationship()
