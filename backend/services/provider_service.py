"""Provider service abstraction layer.

Queries provider directory from PostgreSQL via ProviderRepository.
Seeds initial compatibility provider data if unseeded, explicitly marking records as demo data (`isDemo: True`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.database import session_scope
from backend.repositories.provider_repository import ProviderRepository
from backend.services.mock_cms_data import MOCK_PROVIDERS


def provider_model_to_dict(provider) -> dict[str, Any]:
    """Convert Provider ORM instance to JSON-ready dictionary."""
    return {
        "id": provider.id,
        "name": provider.name,
        "type": provider.type,
        "address": provider.address,
        "cityStateZip": provider.city_state_zip,
        "distanceMiles": provider.distance_miles,
        "isOpen247": provider.is_open_247,
        "operatingHours": provider.operating_hours,
        "currentWaitTimeMins": provider.current_wait_time_mins,
        "copayAmount": provider.copay_amount,
        "averageTotalCost": provider.average_total_cost,
        "phone": provider.phone,
        "telehealthUrl": provider.telehealth_url,
        "acceptsMedicare": provider.accepts_medicare,
        "services": provider.services or [],
        "isDemo": provider.is_demo,
    }


def _execute_get_providers(
    session: Session,
    provider_type: str | None = None,
    max_distance: float | None = None,
) -> list[dict[str, Any]]:
    repo = ProviderRepository(session)
    repo.seed_providers(MOCK_PROVIDERS)
    providers = repo.get_all(provider_type=provider_type, max_distance=max_distance)
    return [provider_model_to_dict(p) for p in providers]


def get_providers(
    provider_type: str | None = None,
    max_distance: float | None = None,
    session: Session | None = None,
) -> list[dict[str, Any]]:
    """Return provider directory filtered by care type and maximum distance."""
    if session is not None:
        return _execute_get_providers(session, provider_type=provider_type, max_distance=max_distance)

    with session_scope() as sess:
        return _execute_get_providers(sess, provider_type=provider_type, max_distance=max_distance)
