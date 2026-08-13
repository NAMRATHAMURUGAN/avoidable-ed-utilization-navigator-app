"""Framework-independent member data access."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.member import Member


class MemberRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_bene_id(self, bene_id: str) -> Member | None:
        return self._session.scalar(select(Member).where(Member.bene_id == bene_id))

    def add(self, member: Member) -> Member:
        self._session.add(member)
        return member
