"""Framework-independent repository for the PATIENT <-> synthetic CMS Member
demo association (see backend/models/patient_member_link.py)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.member import Member
from backend.models.patient_member_link import PatientMemberLink


class PatientMemberLinkRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_user_id(self, user_id: int) -> PatientMemberLink | None:
        return self._session.scalar(
            select(PatientMemberLink).where(PatientMemberLink.user_id == user_id)
        )

    def get_next_unclaimed_member_id(self) -> int | None:
        """Lowest Member.id not yet claimed by any patient link -- deterministic,
        never random, so assignment is easy to reason about and test. Returns
        None only if every Member is already linked (or none exist), which
        callers must treat as "no enrichment available", never an error."""
        statement = (
            select(Member.id)
            .outerjoin(PatientMemberLink, PatientMemberLink.member_id == Member.id)
            .where(PatientMemberLink.id.is_(None))
            .order_by(Member.id)
            .limit(1)
        )
        return self._session.scalar(statement)

    def create(self, link: PatientMemberLink) -> PatientMemberLink:
        self._session.add(link)
        self._session.flush()
        return link
