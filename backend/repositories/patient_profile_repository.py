"""Framework-independent data access for self-entered patient profiles."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.patient_profile import PatientProfile


class PatientProfileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_user_id(self, user_id: int) -> PatientProfile | None:
        return self._session.scalar(
            select(PatientProfile).where(PatientProfile.user_id == user_id)
        )

    def add(self, profile: PatientProfile) -> PatientProfile:
        """Stage a new profile record for insertion; caller controls the transaction."""
        self._session.add(profile)
        self._session.flush()
        return profile
