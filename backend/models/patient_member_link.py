"""Demo-safe PATIENT <-> synthetic CMS Member association.

A ``PatientMemberLink`` is the ONLY place a login identity (``User``) is
ever connected to a CMS analytical record (``Member``). It is a separate
join table, not a column added to either existing model, so that:

  - ``Member`` (backend/models/member.py) keeps meaning exactly what it has
    always meant -- a CMS-sourced/synthetic analytical population record --
    with no notion of "login" leaking into it.
  - ``User``/``PatientProfile`` keep meaning exactly what they have always
    meant -- a consumer login identity and its self-entered profile -- with
    no notion of "CMS beneficiary" leaking into them.

Each authenticated PATIENT is linked to at most one Member (``user_id`` is
unique), and each Member is claimed by at most one PATIENT (``member_id`` is
unique) so no two demo patients ever appear to share the same synthetic
utilization history. The link is created once, automatically, the first
time it is needed (see backend/services/patient_member_link_service.py) and
is then permanent for that login -- a PATIENT never chooses or supplies
which member they are linked to.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

if TYPE_CHECKING:
    from backend.models.member import Member
    from backend.models.user import User


class PatientMemberLink(Base):
    """One PATIENT login identity <-> one synthetic CMS Member, permanently."""

    __tablename__ = "patient_member_links"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    member_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[User] = relationship()
    member: Mapped[Member] = relationship()
