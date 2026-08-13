"""Shared errors and artifact-schema definitions for ML results."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactSpec:
    """Minimum schema required to safely serve a generated artifact."""

    filename: str
    required_fields: frozenset[str]


class MLResultsServiceError(Exception):
    """Base class for safe, client-facing ML-results service errors."""


class ArtifactNotFoundError(MLResultsServiceError):
    """Raised when a generated artifact is unavailable."""


class ArtifactReadError(MLResultsServiceError):
    """Raised when a generated artifact cannot be parsed."""


class ArtifactSchemaError(MLResultsServiceError):
    """Raised when an artifact lacks its minimum expected schema."""
