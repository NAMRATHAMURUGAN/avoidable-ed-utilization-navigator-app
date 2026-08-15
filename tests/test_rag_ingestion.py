"""Mock-only tests for approved knowledge-base ingestion."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from pathlib import Path

import pytest

from backend.config import RagSettings, get_rag_settings
from backend.rag.embeddings import EmbeddingValidationError, GeminiEmbedder, validate_embeddings
from backend.rag.ingestion import ingest_knowledge_base
from backend.rag.knowledge_base import (
    KnowledgeBaseValidationError,
    build_knowledge_chunks,
    chunk_document,
    discover_markdown_documents,
    load_markdown_document,
    validate_chunk_metadata,
)
from backend.rag.pinecone_service import PineconeKnowledgeIndex, PineconeValidationError


def _settings() -> RagSettings:
    return RagSettings(
        gemini_api_key="test-gemini-key", pinecone_api_key="test-pinecone-key",
        pinecone_index_name="knowledge-index", pinecone_namespace="knowledge-base",
        pinecone_metric="cosine",
        embedding_model="gemini-embedding-2", embedding_dimension=3,
    )


def _write_document(root: Path, name: str = "sample.md", content: str | None = None) -> Path:
    category = root / "care_navigation"
    category.mkdir(parents=True, exist_ok=True)
    path = category / name
    path.write_text(
        content if content is not None else "# Sample navigation\n\n## Purpose\n\nUseful approved knowledge.",
        encoding="utf-8",
    )
    return path


def test_markdown_discovery_includes_only_approved_sources(tmp_path: Path) -> None:
    first = _write_document(tmp_path, "z.md")
    second = _write_document(tmp_path, "a.md")
    (tmp_path / "README.md").write_text("# Guidance", encoding="utf-8")
    (tmp_path / "care_navigation" / "notes.txt").write_text("ignored", encoding="utf-8")
    assert discover_markdown_documents(tmp_path) == [second, first]


def test_project_model_output_documents_are_not_in_the_rag_corpus() -> None:
    root = Path(__file__).resolve().parents[1] / "rag_documents"
    discovered = discover_markdown_documents(root)
    assert all(path.name != "utilization_analytics_patterns.md" for path in discovered)
    assert all("utilization_patterns" not in path.parts for path in discovered)


def test_metadata_is_extracted_from_real_approved_document() -> None:
    root = Path(__file__).resolve().parents[1] / "rag_documents"
    document = load_markdown_document(root / "care_navigation" / "medicare_service_navigation.md", root)
    assert document.document_id == "rag:care_navigation:medicare_service_navigation"
    assert document.category == "care_navigation"
    assert document.title == "Medicare Service Navigation"
    assert document.source_type == "source_backed"
    assert document.source_organization == "Medicare.gov"
    assert document.source_url == "https://www.medicare.gov/coverage/emergency-department-services"
    assert document.version is None


def test_chunk_ids_are_deterministic_and_include_context(tmp_path: Path) -> None:
    path = _write_document(tmp_path, content="# Sample navigation\n\n## Purpose\n\n" + ("A useful sentence. " * 150))
    document = load_markdown_document(path, tmp_path)
    first = chunk_document(document)
    second = chunk_document(document)
    assert [chunk.vector_id for chunk in first] == [chunk.vector_id for chunk in second]
    assert all(chunk.text.startswith("Title: Sample navigation") for chunk in first)
    assert len(first) > 1


def test_title_only_preamble_does_not_become_a_tiny_chunk(tmp_path: Path) -> None:
    path = _write_document(
        tmp_path,
        content="# Sample navigation\n\n## Purpose\n\nThis is substantive knowledge for care navigation.",
    )
    chunks = chunk_document(load_markdown_document(path, tmp_path))
    assert len(chunks) == 1
    assert chunks[0].text.startswith("Title: Sample navigation\nSection: Purpose")
    assert "This is substantive knowledge" in chunks[0].text


def test_meaningful_short_section_is_preserved(tmp_path: Path) -> None:
    path = _write_document(
        tmp_path,
        content="# Sample navigation\n\n## Important note\n\nCall your healthcare team for follow-up questions.",
    )
    chunks = chunk_document(load_markdown_document(path, tmp_path))
    assert len(chunks) == 1
    assert "Call your healthcare team" in chunks[0].text


def test_empty_malformed_and_unsupported_documents_fail(tmp_path: Path) -> None:
    empty = _write_document(tmp_path, "empty.md", "")
    malformed = _write_document(tmp_path, "malformed.md", "No title")
    unsupported = _write_document(tmp_path, "unsupported.txt", "text")
    with pytest.raises(KnowledgeBaseValidationError, match="empty"):
        load_markdown_document(empty, tmp_path)
    with pytest.raises(KnowledgeBaseValidationError, match="level-one title"):
        load_markdown_document(malformed, tmp_path)
    with pytest.raises(KnowledgeBaseValidationError, match="Unsupported"):
        load_markdown_document(unsupported, tmp_path)


def test_embedding_dimension_validation() -> None:
    validate_embeddings([[0.1, 0.2, 0.3]], 3, expected_count=1)
    with pytest.raises(EmbeddingValidationError, match="dimension 2"):
        validate_embeddings([[0.1, 0.2]], 3, expected_count=1)
    with pytest.raises(EmbeddingValidationError, match="count"):
        validate_embeddings([[0.1, 0.2, 0.3]], 3, expected_count=2)


def _mock_google_genai(monkeypatch: pytest.MonkeyPatch, vectors: list[list[float]]) -> list[dict[str, object]]:
    """Install a no-network SDK double and expose the embed request payload."""
    calls: list[dict[str, object]] = []

    class FakePart:
        @classmethod
        def from_text(cls, *, text: str) -> dict[str, str]:
            return {"text": text}

    class FakeContent:
        def __init__(self, *, parts: list[dict[str, str]]) -> None:
            self.parts = parts

    class FakeModels:
        def embed_content(self, **kwargs: object) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(embeddings=[SimpleNamespace(values=vector) for vector in vectors])

    class FakeClient:
        def __init__(self, *, api_key: str) -> None:
            self.models = FakeModels()

    fake_types = SimpleNamespace(
        Content=FakeContent,
        Part=FakePart,
        EmbedContentConfig=lambda **kwargs: kwargs,
    )
    fake_genai = ModuleType("google.genai")
    fake_genai.Client = FakeClient
    fake_genai.types = fake_types
    fake_google = ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    return calls


def test_gemini_embedder_single_text_returns_one_768_dimension_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mock_google_genai(monkeypatch, [[0.0] * 768])
    result = GeminiEmbedder(api_key="test", model="gemini-embedding-2", dimension=768).embed(["one text"])
    assert result == [[0.0] * 768]
    assert len(calls) == 1
    assert len(calls[0]["contents"]) == 1


def test_gemini_embedder_sends_each_text_as_separate_content_and_returns_matching_embeddings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mock_google_genai(monkeypatch, [[float(index)] * 768 for index in range(3)])
    result = GeminiEmbedder(api_key="test", model="gemini-embedding-2", dimension=768).embed(["first", "second", "third"])
    assert len(result) == 3
    assert all(len(vector) == 768 for vector in result)
    contents = calls[0]["contents"]
    assert [content.parts[0]["text"] for content in contents] == ["first", "second", "third"]
    assert calls[0]["config"] == {"output_dimensionality": 768}


def test_gemini_embedder_raises_for_mismatched_response_count(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_google_genai(monkeypatch, [[0.0] * 768])
    embedder = GeminiEmbedder(api_key="test", model="gemini-embedding-2", dimension=768)
    with pytest.raises(EmbeddingValidationError, match="count"):
        embedder.embed(["first", "second"])


def test_invalid_chunk_metadata_is_rejected() -> None:
    with pytest.raises(KnowledgeBaseValidationError, match="document_id"):
        validate_chunk_metadata({"document_id": "", "source_file": "a.md", "category": "care_navigation",
                                 "title": "A", "source_type": "source_backed", "chunk_ordinal": 0})


def test_rag_configuration_requires_values_and_reads_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GEMINI_API_KEY", "PINECONE_API_KEY", "PINECONE_INDEX_NAME"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="PINECONE_API_KEY"):
        get_rag_settings()
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("PINECONE_API_KEY", "p")
    monkeypatch.setenv("PINECONE_INDEX_NAME", "index")
    monkeypatch.setenv("PINECONE_KNOWLEDGE_NAMESPACE", "approved-kb")
    monkeypatch.setenv("RAG_EMBEDDING_DIMENSION", "1536")
    settings = get_rag_settings()
    assert settings.pinecone_namespace == "approved-kb"
    assert settings.embedding_dimension == 1536


class _FakePineconeIndex:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict[str, object]], str]] = []

    def upsert(self, *, vectors: list[dict[str, object]], namespace: str) -> dict[str, int]:
        self.calls.append((vectors, namespace))
        return {"upserted_count": len(vectors)}


class _FakePineconeClient:
    def __init__(self, dimension: int = 3) -> None:
        self.dimension = dimension
        self.index = _FakePineconeIndex()

    def describe_index(self, *, name: str) -> dict[str, object]:
        return {"dimension": self.dimension, "metric": "cosine"}

    def Index(self, name: str) -> _FakePineconeIndex:
        return self.index


def test_pinecone_upsert_uses_shared_namespace_and_compact_metadata(tmp_path: Path) -> None:
    _write_document(tmp_path)
    chunks = build_knowledge_chunks(tmp_path)
    client = _FakePineconeClient()
    index = PineconeKnowledgeIndex(_settings(), client=client)
    index.validate_index()
    assert index.upsert(chunks, [[0.1, 0.2, 0.3] for _ in chunks]) == len(chunks)
    records, namespace = client.index.calls[0]
    assert namespace == "knowledge-base"
    assert all(record["metadata"]["category"] == "care_navigation" for record in records)
    assert all("text" in record["metadata"] for record in records)
    assert all(value is not None for record in records for value in record["metadata"].values())
    with pytest.raises(PineconeValidationError, match="does not match"):
        PineconeKnowledgeIndex(_settings(), client=_FakePineconeClient(dimension=4)).validate_index()


class _FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(index), 0.0, 1.0] for index, _ in enumerate(texts)]


class _FakeKnowledgeIndex:
    def __init__(self) -> None:
        self.records: dict[str, list[float]] = {}
        self.validated = 0

    def validate_index(self) -> None:
        self.validated += 1

    def upsert(self, chunks: list[object], vectors: list[list[float]]) -> int:
        for chunk, vector in zip(chunks, vectors, strict=True):
            self.records[chunk.vector_id] = vector
        return len(chunks)


def test_repeated_ingestion_is_idempotent_with_deterministic_ids(tmp_path: Path) -> None:
    _write_document(tmp_path)
    index = _FakeKnowledgeIndex()
    first = ingest_knowledge_base(tmp_path, settings=_settings(), embedder=_FakeEmbedder(), index=index)
    second = ingest_knowledge_base(tmp_path, settings=_settings(), embedder=_FakeEmbedder(), index=index)
    assert first == second
    assert first.upserted_count == len(index.records)
    assert index.validated == 2
