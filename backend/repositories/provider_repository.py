"""Framework-independent repository for provider directory data."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from backend.models.provider import Provider


class ProviderRepository:
    """Data access repository for provider directory records."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_all(
        self,
        provider_type: str | None = None,
        max_distance: float | None = None,
    ) -> list[Provider]:
        statement = select(Provider)

        if provider_type and provider_type != "ALL":
            statement = statement.where(Provider.type == provider_type)

        if max_distance is not None:
            statement = statement.where(Provider.distance_miles <= max_distance)

        statement = statement.order_by(Provider.distance_miles)
        try:
            return list(self._session.scalars(statement).all())
        except (ProgrammingError, OperationalError):
            self._session.rollback()
            return []

    def get_by_id(self, provider_id: str) -> Provider | None:
        try:
            return self._session.scalar(select(Provider).where(Provider.id == provider_id))
        except (ProgrammingError, OperationalError):
            self._session.rollback()
            return None
