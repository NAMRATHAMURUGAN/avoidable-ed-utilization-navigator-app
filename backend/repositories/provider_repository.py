"""Framework-independent repository for provider directory data."""

from __future__ import annotations

from typing import Any

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

    def seed_providers(self, providers_data: list[dict[str, Any]]) -> int:
        """Seed provider table if empty, explicitly setting is_demo flag."""
        try:
            existing_count = self._session.scalar(select(Provider.id))
            if existing_count is not None:
                return 0
        except (ProgrammingError, OperationalError):
            self._session.rollback()
            try:
                bind = self._session.get_bind()
                Provider.__table__.create(bind, checkfirst=True)
            except Exception:
                return 0

        inserted = 0
        for p in providers_data:
            provider = Provider(
                id=p["id"],
                name=p["name"],
                type=p["type"],
                address=p["address"],
                city_state_zip=p["cityStateZip"],
                distance_miles=float(p["distanceMiles"]),
                is_open_247=bool(p.get("isOpen247", False)),
                operating_hours=p["operatingHours"],
                current_wait_time_mins=int(p.get("currentWaitTimeMins", 0)),
                copay_amount=float(p.get("copayAmount", 0.0)),
                average_total_cost=float(p.get("averageTotalCost", 0.0)),
                phone=p["phone"],
                telehealth_url=p.get("telehealthUrl"),
                accepts_medicare=bool(p.get("acceptsMedicare", True)),
                services=p.get("services", []),
                is_demo=True,  # Explicitly labeled as demo/compatibility data
            )
            self._session.add(provider)
            inserted += 1
        try:
            self._session.flush()
        except (ProgrammingError, OperationalError):
            self._session.rollback()
            return 0
        return inserted
