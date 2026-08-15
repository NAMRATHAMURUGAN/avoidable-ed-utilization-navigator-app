"""Explicit, offline orchestration for shared knowledge-base ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.config import RagSettings, get_rag_settings
from backend.rag.embeddings import GeminiEmbedder, validate_embeddings
from backend.rag.knowledge_base import DEFAULT_SOURCE_DIRECTORY, KnowledgeChunk, build_knowledge_chunks
from backend.rag.pinecone_service import PineconeKnowledgeIndex


@dataclass(frozen=True)
class IngestionResult:
    document_count: int
    chunk_count: int
    upserted_count: int


def ingest_knowledge_base(
    source_directory: Path = DEFAULT_SOURCE_DIRECTORY,
    *,
    settings: RagSettings | None = None,
    embedder: GeminiEmbedder | None = None,
    index: PineconeKnowledgeIndex | None = None,
) -> IngestionResult:
    """Validate, embed, and upsert approved KB sources; never runs on app startup."""
    settings = settings or get_rag_settings()
    chunks: list[KnowledgeChunk] = build_knowledge_chunks(source_directory)
    embedder = embedder or GeminiEmbedder(
        api_key=settings.gemini_api_key, model=settings.embedding_model, dimension=settings.embedding_dimension,
    )
    vectors = embedder.embed([chunk.text for chunk in chunks])
    validate_embeddings(vectors, settings.embedding_dimension, expected_count=len(chunks))
    index = index or PineconeKnowledgeIndex(settings)
    index.validate_index()
    return IngestionResult(
        document_count=len({chunk.metadata["document_id"] for chunk in chunks}),
        chunk_count=len(chunks), upserted_count=index.upsert(chunks, vectors),
    )
