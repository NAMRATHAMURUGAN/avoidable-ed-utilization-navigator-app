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


@dataclass(frozen=True)
class RagSettings:
    """Configuration for the offline, shared knowledge-base ingestion job."""

    gemini_api_key: str
    pinecone_api_key: str
    pinecone_index_name: str
    pinecone_namespace: str
    pinecone_metric: str
    embedding_model: str
    embedding_dimension: int


def get_rag_settings() -> RagSettings:
    """Read RAG ingestion configuration without logging any secret values."""
    required = {
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
        "PINECONE_API_KEY": os.getenv("PINECONE_API_KEY"),
        "PINECONE_INDEX_NAME": os.getenv("PINECONE_INDEX_NAME"),
    }
    missing = [name for name, value in required.items() if not value or value.startswith("your_")]
    if missing:
        raise RuntimeError("RAG ingestion requires these environment variables: " + ", ".join(missing) + ".")

    raw_dimension = os.getenv("RAG_EMBEDDING_DIMENSION", "768")
    try:
        embedding_dimension = int(raw_dimension)
    except ValueError as error:
        raise RuntimeError("RAG_EMBEDDING_DIMENSION must be a positive integer.") from error
    if embedding_dimension <= 0:
        raise RuntimeError("RAG_EMBEDDING_DIMENSION must be a positive integer.")

    return RagSettings(
        gemini_api_key=required["GEMINI_API_KEY"] or "",
        pinecone_api_key=required["PINECONE_API_KEY"] or "",
        pinecone_index_name=required["PINECONE_INDEX_NAME"] or "",
        pinecone_namespace=os.getenv("PINECONE_KNOWLEDGE_NAMESPACE", "knowledge-base"),
        pinecone_metric=os.getenv("PINECONE_INDEX_METRIC", "cosine"),
        embedding_model=os.getenv("RAG_EMBEDDING_MODEL", "gemini-embedding-2"),
        embedding_dimension=embedding_dimension,
    )
