"""SQLAlchemy engine, session factory, and declarative base.

The engine is initialized lazily so declarative models can be imported for
metadata inspection without requiring a live database connection.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import get_database_settings


class Base(DeclarativeBase):
    """Base class for all database models."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine for ``DATABASE_URL``."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_database_settings().database_url,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide SQLAlchemy 2.x session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional session for future framework-independent use."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_database_schema() -> None:
    """Create this project's tables only; existing tables are not dropped."""
    # Importing registers every declarative model with Base.metadata.
    import backend.models  # noqa: F401

    Base.metadata.create_all(get_engine())
