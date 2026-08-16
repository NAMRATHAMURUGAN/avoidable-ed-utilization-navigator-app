"""Mock-only tests for read-only knowledge-base retrieval."""

from __future__ import annotations

import pytest

from backend.config import RagSettings
from backend.rag.pinecone_service import PineconeKnowledgeIndex
from backend.rag.retrieval import KnowledgeRetriever, RetrievalError, RetrievalValidationError


def _settings() -> RagSettings:
    return RagSettings(
        gemini_api_key="test-gemini-key", pinecone_api_key="test-pinecone-key",
        pinecone_index_name="avoidable-ed-rag", pinecone_namespace="knowledge-base",
        pinecone_metric="cosine", embedding_model="gemini-embedding-2", embedding_dimension=768,
    )


class _FakeEmbedder:
    def __init__(self, vectors: list[list[float]] | Exception) -> None:
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        if isinstance(self.vectors, Exception):
            raise self.vectors
        return self.vectors


class _FakeIndex:
    def __init__(self, matches: list[object] | Exception) -> None:
        self.matches = matches
        self.validated = 0
        self.query_calls: list[tuple[list[float], int]] = []

    def validate_index(self) -> None:
        self.validated += 1

    def query(self, vector: list[float], *, top_k: int) -> list[object]:
        self.query_calls.append((vector, top_k))
        if isinstance(self.matches, Exception):
            raise self.matches
        return self.matches


class _FakePineconeDataIndex:
    def __init__(self) -> None:
        self.query_kwargs: dict[str, object] | None = None

    def query(self, **kwargs: object) -> dict[str, list[object]]:
        self.query_kwargs = kwargs
        return {"matches": []}


class _FakePineconeClient:
    def __init__(self) -> None:
        self.data_index = _FakePineconeDataIndex()

    def Index(self, name: str) -> _FakePineconeDataIndex:
        assert name == "avoidable-ed-rag"
        return self.data_index


def _retriever(
    *,
    vectors: list[list[float]] | Exception = [[0.1] * 768],
    matches: list[object] | Exception = [],
) -> tuple[KnowledgeRetriever, _FakeEmbedder, _FakeIndex]:
    embedder = _FakeEmbedder(vectors)
    index = _FakeIndex(matches)
    return KnowledgeRetriever(settings=_settings(), embedder=embedder, index=index), embedder, index


def test_retrieve_returns_scores_text_and_provenance_in_knowledge_namespace() -> None:
    match = {
        "id": "kb-123",
        "score": 0.91,
        "metadata": {
            "text": "Approved emergency-care knowledge.", "document_id": "rag:emergency:care",
            "source_file": "emergency_guidelines/emergency_care_safety.md",
            "category": "emergency_guidelines", "title": "Emergency-Care Safety Boundary",
            "source_type": "source_backed", "chunk_ordinal": 2,
            "source_organization": "Medicare.gov", "source_url": "https://example.test/source",
        },
    }
    retriever, embedder, index = _retriever(matches=[match])

    results = retriever.retrieve("  emergency and urgent care  ", top_k=3)

    assert embedder.calls == [["emergency and urgent care"]]
    assert index.validated == 1
    assert index.query_calls == [([0.1] * 768, 3)]
    assert len(results) == 1
    assert results[0].chunk_id == "kb-123"
    assert results[0].score == 0.91
    assert results[0].text == "Approved emergency-care knowledge."
    assert results[0].document_id == "rag:emergency:care"
    assert results[0].source_url == "https://example.test/source"
    assert results[0].metadata == match["metadata"]


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_queries_are_rejected_without_external_calls(query: str) -> None:
    retriever, embedder, index = _retriever()
    with pytest.raises(RetrievalValidationError, match="non-empty"):
        retriever.retrieve(query)
    assert embedder.calls == []
    assert index.query_calls == []


@pytest.mark.parametrize("top_k", [0, -1, 21, True, "5"])
def test_invalid_top_k_is_rejected_without_external_calls(top_k: object) -> None:
    retriever, embedder, index = _retriever()
    with pytest.raises(RetrievalValidationError, match="top_k"):
        retriever.retrieve("valid query", top_k=top_k)  # type: ignore[arg-type]
    assert embedder.calls == []
    assert index.query_calls == []


def test_embedding_failure_prevents_pinecone_query() -> None:
    retriever, _, index = _retriever(vectors=RuntimeError("Gemini unavailable"))
    with pytest.raises(RetrievalError, match="embedding failed"):
        retriever.retrieve("valid query")
    assert index.validated == 0
    assert index.query_calls == []


def test_invalid_query_embedding_dimension_prevents_pinecone_query() -> None:
    retriever, _, index = _retriever(vectors=[[0.1] * 767])
    with pytest.raises(RetrievalError, match="embedding failed"):
        retriever.retrieve("valid query")
    assert index.query_calls == []


def test_pinecone_failure_is_controlled_and_does_not_fabricate_results() -> None:
    retriever, _, index = _retriever(matches=RuntimeError("Pinecone unavailable"))
    with pytest.raises(RetrievalError, match="Pinecone query failed"):
        retriever.retrieve("valid query")
    assert index.query_calls == [([0.1] * 768, 5)]


def test_no_matches_returns_empty_list() -> None:
    retriever, _, index = _retriever(matches=[])
    assert retriever.retrieve("care coordination") == []
    assert index.query_calls == [([0.1] * 768, 5)]


def test_pinecone_adapter_queries_only_the_configured_knowledge_namespace() -> None:
    client = _FakePineconeClient()
    index = PineconeKnowledgeIndex(_settings(), client=client)
    assert index.query([0.1] * 768, top_k=4) == []
    assert client.data_index.query_kwargs == {
        "vector": [0.1] * 768,
        "top_k": 4,
        "namespace": "knowledge-base",
        "include_metadata": True,
        "include_values": False,
    }


def test_malformed_metadata_is_safe_and_preserves_valid_match_fields() -> None:
    retriever, _, _ = _retriever(matches=[{"id": "kb-1", "score": 0.4, "metadata": "invalid"}])
    result = retriever.retrieve("care coordination")[0]
    assert result.chunk_id == "kb-1"
    assert result.score == 0.4
    assert result.text is None
    assert result.metadata == {}
