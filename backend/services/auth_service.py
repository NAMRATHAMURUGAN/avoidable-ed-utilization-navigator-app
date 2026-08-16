"""Authentication service: registration, login, and Flask session management.

A ``User`` is a login identity, distinct from a CMS ``Member``/beneficiary
record (see backend/models/user.py). This module owns password hashing,
credential verification, and Flask session handling so that routes stay thin
and repositories stay pure data-access, following the pattern already used by
triage_service.py and patient_service.py in this project.

This module has no knowledge of, and never calls, backend/safety/engine.py.
Authentication controls access to application functionality only; it must
never influence a clinical/safety decision.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import session as flask_session
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from backend.database import session_scope
from backend.models.user import ALLOWED_ROLES, User
from backend.repositories.user_repository import UserRepository


MIN_PASSWORD_LENGTH = 8
SESSION_USER_ID_KEY = "user_id"


class AuthValidationError(ValueError):
    """Raised for invalid registration input. Safe to show to API callers."""


class AuthenticationError(ValueError):
    """Raised when credentials are invalid. Message is intentionally generic
    so a caller cannot distinguish "wrong password" from "no such account"."""


def normalize_email(email: Any) -> str:
    """Normalize an email to a trimmed, lowercase string."""
    if not isinstance(email, str):
        raise AuthValidationError("A valid email is required.")
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        raise AuthValidationError("A valid email is required.")
    return normalized


def _validate_password(password: Any) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise AuthValidationError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )
    return password


def _validate_role(role: Any) -> str:
    if not isinstance(role, str) or role.strip().upper() not in ALLOWED_ROLES:
        raise AuthValidationError(f"Role must be one of: {', '.join(ALLOWED_ROLES)}.")
    return role.strip().upper()


def user_to_safe_dict(user: User) -> dict[str, Any]:
    """Return only non-sensitive user fields. Never includes password_hash."""
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
    }


def _execute_register_user(session: Session, email: Any, password: Any, role: Any) -> User:
    normalized_email = normalize_email(email)
    validated_password = _validate_password(password)
    validated_role = _validate_role(role)

    repo = UserRepository(session)
    if repo.get_by_email(normalized_email) is not None:
        raise AuthValidationError("An account with this email already exists.")

    user = User(
        email=normalized_email,
        password_hash=generate_password_hash(validated_password),
        role=validated_role,
        created_at=datetime.now(timezone.utc),
    )
    repo.add(user)
    try:
        session.flush()
    except IntegrityError:
        # A concurrent request may have registered this exact email in the
        # narrow window between the pre-check above and this insert. Roll
        # back the failed statement, then confirm it was genuinely a
        # duplicate-email conflict before reporting it as one, so an
        # unrelated integrity failure is never silently mislabeled.
        session.rollback()
        if repo.get_by_email(normalized_email) is not None:
            raise AuthValidationError("An account with this email already exists.")
        raise
    return user


def register_user(
    email: Any, password: Any, role: Any, session: Session | None = None
) -> User:
    """Validate input and create a new user. Raises AuthValidationError on bad input."""
    if session is not None:
        return _execute_register_user(session, email, password, role)

    with session_scope() as sess:
        return _execute_register_user(sess, email, password, role)


def _execute_authenticate_user(session: Session, email: Any, password: Any) -> User:
    normalized_email = normalize_email(email)
    if not isinstance(password, str) or not password:
        raise AuthenticationError("Invalid email or password.")

    repo = UserRepository(session)
    user = repo.get_by_email(normalized_email)
    if user is None or not check_password_hash(user.password_hash, password):
        # Deliberately identical error for "no such account" and "wrong
        # password" so a caller cannot enumerate registered email addresses.
        raise AuthenticationError("Invalid email or password.")
    return user


def authenticate_user(
    email: Any, password: Any, session: Session | None = None
) -> User:
    """Verify credentials. Raises AuthenticationError with a generic message on failure."""
    if session is not None:
        return _execute_authenticate_user(session, email, password)

    with session_scope() as sess:
        return _execute_authenticate_user(sess, email, password)


def create_session(user: User) -> None:
    """Start a Flask session for the given authenticated user."""
    flask_session.clear()
    flask_session[SESSION_USER_ID_KEY] = user.id
    flask_session.permanent = False


def clear_session() -> None:
    """Clear the current Flask session (logout)."""
    flask_session.clear()


def _execute_get_current_user(session: Session) -> User | None:
    user_id = flask_session.get(SESSION_USER_ID_KEY)
    if user_id is None:
        return None
    return UserRepository(session).get_by_id(user_id)


def get_current_user(session: Session | None = None) -> User | None:
    """Return the User for the current Flask session, or None if unauthenticated."""
    if session is not None:
        return _execute_get_current_user(session)

    with session_scope() as sess:
        return _execute_get_current_user(sess)
