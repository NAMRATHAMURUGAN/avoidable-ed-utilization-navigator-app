"""Small Pinecone adapter for explicit knowledge-base indexing only."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from backend.config import RagSettings
from backend.rag.knowledge_base import KnowledgeChunk


class PineconeValidationError(ValueError):
    """Raised before an incompatible or incomplete Pinecone write."""


def _field(description: Any, name: str) -> Any:
    return description.get(name) if isinstance(description, dict) else getattr(description, name, None)


class PineconeKnowledgeIndex:
    def __init__(self, settings: RagSettings, client: Any | None = None) -> None:
        self.settings = settings
        if client is None:
            try:
                from pinecone import Pinecone
            except ImportError as error:
                raise RuntimeError("pinecone is required for RAG ingestion; install requirements.txt first.") from error
            client = Pinecone(api_key=settings.pinecone_api_key)
        self.client = client

    def validate_index(self) -> None:
        description = self.client.describe_index(name=self.settings.pinecone_index_name)
        dimension = _field(description, "dimension")
        if dimension != self.settings.embedding_dimension:
            raise PineconeValidationError(
                f"Pinecone index dimension {dimension!r} does not match RAG_EMBEDDING_DIMENSION "
                f"{self.settings.embedding_dimension}."
            )
        metric = _field(description, "metric")
        if metric != self.settings.pinecone_metric:
            raise PineconeValidationError(
                f"Pinecone index metric {metric!r} does not match PINECONE_INDEX_METRIC "
                f"{self.settings.pinecone_metric!r}."
            )

    def upsert(self, chunks: Sequence[KnowledgeChunk], vectors: Sequence[Sequence[float]]) -> int:
        if len(chunks) != len(vectors):
            raise PineconeValidationError("Chunk and embedding counts do not match.")
        records = [
            {
                "id": chunk.vector_id,
                "values": list(vector),
                # Pinecone metadata does not accept null values. Optional provenance
                # fields remain null in the in-memory document model and are omitted here.
                "metadata": {
                    key: value for key, value in {**chunk.metadata, "text": chunk.text}.items()
                    if value is not None
                },
            }
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        if not records:
            raise PineconeValidationError("No vector records were supplied for upsert.")
        index = self.client.Index(self.settings.pinecone_index_name)
        result = index.upsert(vectors=records, namespace=self.settings.pinecone_namespace)
        upserted = _field(result, "upserted_count")
        if upserted is not None and upserted != len(records):
            raise PineconeValidationError(f"Pinecone upsert reported {upserted} records; expected {len(records)}.")
        return len(records)
