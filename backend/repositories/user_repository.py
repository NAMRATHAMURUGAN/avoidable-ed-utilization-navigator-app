"""Framework-independent user data access."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.user import User


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_email(self, email: str) -> User | None:
        return self._session.scalar(select(User).where(User.email == email))

    def get_by_id(self, user_id: int) -> User | None:
        return self._session.scalar(select(User).where(User.id == user_id))

    def add(self, user: User) -> User:
        """Stage a new user record for insertion; caller controls the transaction."""
        self._session.add(user)
        return user
