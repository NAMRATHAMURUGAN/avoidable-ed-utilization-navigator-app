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
