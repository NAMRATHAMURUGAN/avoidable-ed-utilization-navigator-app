"""Read-only retrieval of approved chunks from the shared knowledge index."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from typing import Any

from backend.config import RagSettings, get_rag_settings
from backend.rag.embeddings import GeminiEmbedder, validate_embeddings
from backend.rag.pinecone_service import PineconeKnowledgeIndex


DEFAULT_TOP_K = 5
MAX_TOP_K = 20


class RetrievalValidationError(ValueError):
    """Raised for invalid retrieval input before external calls occur."""


class RetrievalError(RuntimeError):
    """Raised when an external retrieval dependency fails without fabricating results."""


@dataclass(frozen=True)
class RetrievedChunk:
    """A knowledge-index match and its stored provenance metadata."""

    chunk_id: str | None
    text: str | None
    score: float | None
    document_id: str | None
    source_file: str | None
    category: str | None
    title: str | None
    source_type: str | None
    chunk_ordinal: int | None
    source_organization: str | None
    source_url: str | None
    version: str | None
    metadata: dict[str, Any]


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _optional_string(metadata: Mapping[str, Any], field: str) -> str | None:
    value = metadata.get(field)
    return value if isinstance(value, str) else None


def _retrieved_chunk(match: Any) -> RetrievedChunk:
    raw_metadata = _field(match, "metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
    raw_id = _field(match, "id")
    raw_score = _field(match, "score")
    return RetrievedChunk(
        chunk_id=raw_id if isinstance(raw_id, str) else None,
        text=_optional_string(metadata, "text"),
        score=float(raw_score) if isinstance(raw_score, Real) and not isinstance(raw_score, bool) else None,
        document_id=_optional_string(metadata, "document_id"),
        source_file=_optional_string(metadata, "source_file"),
        category=_optional_string(metadata, "category"),
        title=_optional_string(metadata, "title"),
        source_type=_optional_string(metadata, "source_type"),
        chunk_ordinal=metadata.get("chunk_ordinal") if isinstance(metadata.get("chunk_ordinal"), int) else None,
        source_organization=_optional_string(metadata, "source_organization"),
        source_url=_optional_string(metadata, "source_url"),
        version=_optional_string(metadata, "version"),
        metadata=metadata,
    )


class KnowledgeRetriever:
    """Embed a query and retrieve existing knowledge chunks; never generates an answer."""

    def __init__(
        self,
        *,
        settings: RagSettings | None = None,
        embedder: GeminiEmbedder | None = None,
        index: PineconeKnowledgeIndex | None = None,
    ) -> None:
        self.settings = settings or get_rag_settings()
        self.embedder = embedder or GeminiEmbedder(
            api_key=self.settings.gemini_api_key,
            model=self.settings.embedding_model,
            dimension=self.settings.embedding_dimension,
        )
        self.index = index or PineconeKnowledgeIndex(self.settings)

    def retrieve(self, query: str, *, top_k: int = DEFAULT_TOP_K) -> list[RetrievedChunk]:
        if not isinstance(query, str) or not query.strip():
            raise RetrievalValidationError("Retrieval query must be a non-empty string.")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= MAX_TOP_K:
            raise RetrievalValidationError(f"top_k must be an integer between 1 and {MAX_TOP_K}.")

        try:
            vectors = self.embedder.embed([query.strip()])
            validate_embeddings(vectors, self.settings.embedding_dimension, expected_count=1)
        except Exception as error:
            raise RetrievalError("Query embedding failed.") from error

        try:
            self.index.validate_index()
            matches = self.index.query(vectors[0], top_k=top_k)
        except Exception as error:
            raise RetrievalError("Pinecone query failed.") from error
        return [_retrieved_chunk(match) for match in matches]
