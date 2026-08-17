"""Self-entered patient profile service: read/upsert for the authenticated user.

A ``PatientProfile`` is keyed strictly by ``user_id`` (the caller's own login
identity, from ``g.current_user``/``get_current_user()``) -- there is no
caller-suppliable identifier, so one user's profile can never be read or
written by another. This module has no knowledge of, and never touches,
``backend/models/member.py`` (the synthetic CMS beneficiary population) or
any triage/safety/ML code.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.database import session_scope
from backend.models.patient_profile import PatientProfile
from backend.repositories.patient_profile_repository import PatientProfileRepository

MAX_NAME_LENGTH = 255
MAX_ZIP_LENGTH = 16
MAX_CONTACT_LENGTH = 255
MAX_CHOICE_LENGTH = 64
MIN_AGE = 1
MAX_AGE = 120

_STRING_FIELDS = {
    "fullName": ("full_name", MAX_NAME_LENGTH),
    "zipCode": ("zip_code", MAX_ZIP_LENGTH),
    "contactInfo": ("contact_info", MAX_CONTACT_LENGTH),
    "insuranceStatus": ("insurance_status", MAX_CHOICE_LENGTH),
    "preferredCareSetting": ("preferred_care_setting", MAX_CHOICE_LENGTH),
    "communicationPreference": ("communication_preference", MAX_CHOICE_LENGTH),
}


class ProfileValidationError(ValueError):
    """Raised for invalid profile input. Safe to show to API callers."""


def profile_to_dict(profile: PatientProfile | None) -> dict[str, Any]:
    """Return the frontend-facing profile shape. ``exists`` distinguishes a
    genuinely-saved profile from an honest empty/default state for a caller
    who has never saved one -- the frontend must never substitute placeholder
    identity data for a missing row."""
    if profile is None:
        return {
            "exists": False,
            "fullName": None,
            "age": None,
            "zipCode": None,
            "contactInfo": None,
            "insuranceStatus": None,
            "preferredCareSetting": None,
            "communicationPreference": None,
            "updatedAt": None,
        }
    return {
        "exists": True,
        "fullName": profile.full_name,
        "age": profile.age,
        "zipCode": profile.zip_code,
        "contactInfo": profile.contact_info,
        "insuranceStatus": profile.insurance_status,
        "preferredCareSetting": profile.preferred_care_setting,
        "communicationPreference": profile.communication_preference,
        "updatedAt": profile.updated_at.isoformat() if profile.updated_at else None,
    }


def _validate_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize incoming profile fields. Every field is
    optional on a given save (partial updates are allowed), but any field
    that is present must be well-formed."""
    validated: dict[str, Any] = {}

    for payload_key, (column, max_length) in _STRING_FIELDS.items():
        if payload_key not in data or data[payload_key] is None:
            continue
        value = data[payload_key]
        if not isinstance(value, str):
            raise ProfileValidationError(f"{payload_key} must be a string.")
        trimmed = value.strip()
        if len(trimmed) > max_length:
            raise ProfileValidationError(f"{payload_key} must not exceed {max_length} characters.")
        validated[column] = trimmed or None

    if "age" in data and data["age"] is not None:
        age_raw = data["age"]
        if isinstance(age_raw, bool) or not isinstance(age_raw, (int, str)):
            raise ProfileValidationError("age must be a number.")
        try:
            age = int(age_raw)
        except (TypeError, ValueError):
            raise ProfileValidationError("age must be a number.")
        if age < MIN_AGE or age > MAX_AGE:
            raise ProfileValidationError(f"age must be between {MIN_AGE} and {MAX_AGE}.")
        validated["age"] = age

    return validated


def _execute_get_profile(session: Session, user_id: int) -> dict[str, Any]:
    profile = PatientProfileRepository(session).get_by_user_id(user_id)
    return profile_to_dict(profile)


def get_profile(user_id: int, session: Session | None = None) -> dict[str, Any]:
    """Return the authenticated caller's own profile, or an honest empty state."""
    if session is not None:
        return _execute_get_profile(session, user_id)

    with session_scope() as sess:
        return _execute_get_profile(sess, user_id)


def _execute_save_profile(session: Session, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
    validated = _validate_fields(data)

    repo = PatientProfileRepository(session)
    profile = repo.get_by_user_id(user_id)
    now = datetime.now(timezone.utc)

    if profile is None:
        profile = PatientProfile(
            user_id=user_id,
            created_at=now,
            updated_at=now,
            **validated,
        )
        repo.add(profile)
    else:
        for column, value in validated.items():
            setattr(profile, column, value)
        profile.updated_at = now

    session.flush()
    return profile_to_dict(profile)


def save_profile(
    user_id: int, data: dict[str, Any], session: Session | None = None
) -> dict[str, Any]:
    """Create or update the authenticated caller's own profile.

    Raises ProfileValidationError on malformed input.
    """
    if session is not None:
        return _execute_save_profile(session, user_id, data)

    with session_scope() as sess:
        return _execute_save_profile(sess, user_id, data)
