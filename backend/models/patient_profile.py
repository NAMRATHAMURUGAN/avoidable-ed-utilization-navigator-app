"""Patient self-entered profile model.

A ``PatientProfile`` holds personal details a PATIENT enters about themselves
(name, age, ZIP, contact, coverage, and care preferences) for use inside their
own care-navigation sessions. It is deliberately separate from ``Member``
(backend/models/member.py), which represents a synthetic CMS beneficiary/
analytical population record used by the PAYER side of the application. A
``PatientProfile`` is always linked to exactly one ``User`` (self-entered
identity data), never to a ``Member``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

if TYPE_CHECKING:
    from backend.models.user import User


class PatientProfile(Base):
    """Self-entered personal profile for an authenticated PATIENT/PAYER user."""

    __tablename__ = "patient_profiles"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    # One profile per login identity.
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    contact_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    insurance_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preferred_care_setting: Mapped[str | None] = mapped_column(String(64), nullable=True)
    communication_preference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[User] = relationship()
