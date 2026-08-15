"""Environment-backed configuration for the standalone database foundation."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class DatabaseSettings:
    """Database settings required by SQLAlchemy.

    Configuration is intentionally limited to the database layer.  No Flask,
    application-server, ML, or third-party integration settings live here.
    """

    database_url: str


def get_database_settings() -> DatabaseSettings:
    """Read the required PostgreSQL connection URL without exposing it."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL must be set to initialize the PostgreSQL database foundation."
        )
    return DatabaseSettings(database_url=database_url)


@dataclass(frozen=True)
class SecuritySettings:
    """Security settings required by Flask session/cookie signing.

    Configuration is intentionally limited to secret-key material. No
    database, ML, or third-party integration settings live here.
    """

    secret_key: str


def get_security_settings() -> SecuritySettings:
    """Read the required Flask session-signing secret without exposing it.

    Fails loudly rather than falling back to an insecure default, matching
    the fail-fast behavior already used by get_database_settings().
    """
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY must be set to initialize Flask session signing. "
            "Generate a long random value and set it in your environment "
            "(see .env.example)."
        )
    return SecuritySettings(secret_key=secret_key)
