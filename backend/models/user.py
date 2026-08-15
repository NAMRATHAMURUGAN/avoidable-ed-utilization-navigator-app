"""User login-identity model.

A ``User`` is a login identity for the application (email + hashed password +
role). It is intentionally separate from ``Member`` (backend/models/member.py),
which represents a CMS beneficiary/analytical population record. A person
using the application as a patient is not required to correspond to any
``Member`` row, and a ``User`` must never be conflated with one.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


ALLOWED_ROLES = ("PATIENT", "PAYER")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('PATIENT', 'PAYER')", name="ck_users_role_allowed"),
    )

    # Rows are created without a pre-known id (assigned by the database at
    # insert time), so this follows the same cross-dialect autoincrement
    # pattern already used by TriageEncounter/NavigationAction rather than
    # Identity() (which the Member-family models use, since their test
    # fixtures always supply an explicit id).
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
