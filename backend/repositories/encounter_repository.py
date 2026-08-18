"""Framework-independent repository for triage encounters and navigation action history."""

from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from backend.models.encounter import NavigationAction, TriageEncounter


class EncounterRepository:
    """Data access repository for triage encounters and navigation history."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_encounter(self, encounter: TriageEncounter) -> TriageEncounter:
        self._session.add(encounter)
        self._session.flush()
        return encounter

    def get_encounter(self, encounter_id: int) -> TriageEncounter | None:
        try:
            return self._session.scalar(
                select(TriageEncounter).where(TriageEncounter.id == encounter_id)
            )
        except (ProgrammingError, OperationalError):
            self._session.rollback()
            return None

    def get_encounter_by_session_id(self, session_id: str) -> TriageEncounter | None:
        try:
            return self._session.scalar(
                select(TriageEncounter).where(TriageEncounter.session_id == session_id)
            )
        except (ProgrammingError, OperationalError):
            self._session.rollback()
            return None

    def create_action(self, action: NavigationAction) -> NavigationAction:
        self._session.add(action)
        self._session.flush()
        return action

    def get_member_encounters(self, member_id: int) -> list[TriageEncounter]:
        try:
            return list(
                self._session.scalars(
                    select(TriageEncounter)
                    .where(TriageEncounter.member_id == member_id)
                    .order_by(desc(TriageEncounter.created_at))
                ).all()
            )
        except (ProgrammingError, OperationalError):
            self._session.rollback()
            return []

    def get_member_actions(self, member_id: int) -> list[NavigationAction]:
        try:
            return list(
                self._session.scalars(
                    select(NavigationAction)
                    .where(NavigationAction.member_id == member_id)
                    .order_by(desc(NavigationAction.recorded_at))
                ).all()
            )
        except (ProgrammingError, OperationalError):
            self._session.rollback()
            return []

    def get_user_encounters(self, user_id: int) -> list[TriageEncounter]:
        try:
            return list(
                self._session.scalars(
                    select(TriageEncounter)
                    .where(TriageEncounter.user_id == user_id)
                    # id as a tiebreaker: two encounters created in rapid
                    # succession can otherwise share an identical
                    # created_at timestamp, making "most recent first"
                    # non-deterministic on created_at alone.
                    .order_by(desc(TriageEncounter.created_at), desc(TriageEncounter.id))
                ).all()
            )
        except (ProgrammingError, OperationalError):
            self._session.rollback()
            return []

    def get_user_actions(self, user_id: int) -> list[NavigationAction]:
        try:
            return list(
                self._session.scalars(
                    select(NavigationAction)
                    .where(NavigationAction.user_id == user_id)
                    .order_by(desc(NavigationAction.recorded_at), desc(NavigationAction.id))
                ).all()
            )
        except (ProgrammingError, OperationalError):
            self._session.rollback()
            return []

    def get_encounter_actions(self, encounter_id: int) -> list[NavigationAction]:
        try:
            return list(
                self._session.scalars(
                    select(NavigationAction)
                    .where(NavigationAction.encounter_id == encounter_id)
                    .order_by(desc(NavigationAction.recorded_at))
                ).all()
            )
        except (ProgrammingError, OperationalError):
            self._session.rollback()
            return []
