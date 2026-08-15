"""Deterministic parsing and chunking for approved RAG Markdown sources."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIRECTORY = PROJECT_ROOT / "rag_documents"
APPROVED_CATEGORIES = frozenset({
    "care_navigation", "clinical_guidelines", "emergency_guidelines", "emr",
    "patient_education", "rules_procedures", "us_insurance_policies",
})
CHUNK_SIZE = 1_200
CHUNK_OVERLAP = 200


class KnowledgeBaseValidationError(ValueError):
    """Raised when an approved knowledge-base source is unsafe or invalid."""


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    source_file: str
    category: str
    title: str
    source_type: str
    source_organization: str | None
    source_url: str | None
    version: str | None
    content: str


@dataclass(frozen=True)
class KnowledgeChunk:
    vector_id: str
    text: str
    metadata: dict[str, str | int | None]


def validate_chunk_metadata(metadata: dict[str, str | int | None]) -> None:
    """Reject incomplete filtering/provenance metadata before any API call."""
    required_strings = ("document_id", "source_file", "category", "title", "source_type")
    for field in required_strings:
        value = metadata.get(field)
        if not isinstance(value, str) or not value.strip():
            raise KnowledgeBaseValidationError(f"Chunk metadata field {field!r} must be a non-empty string.")
    if metadata["category"] not in APPROVED_CATEGORIES:
        raise KnowledgeBaseValidationError("Chunk metadata category is not approved.")
    if metadata["source_type"] not in {"source_backed", "project_policy", "project_generated", "synthetic"}:
        raise KnowledgeBaseValidationError("Chunk metadata source_type is invalid.")
    if not isinstance(metadata.get("chunk_ordinal"), int) or metadata["chunk_ordinal"] < 0:
        raise KnowledgeBaseValidationError("Chunk metadata chunk_ordinal must be a non-negative integer.")


def discover_markdown_documents(source_directory: Path = DEFAULT_SOURCE_DIRECTORY) -> list[Path]:
    """Return only approved Markdown source documents in deterministic order."""
    if not source_directory.is_dir():
        raise KnowledgeBaseValidationError(f"Knowledge-base directory does not exist: {source_directory}")
    documents: list[Path] = []
    for path in sorted(source_directory.rglob("*")):
        if not path.is_file() or path.name in {"README.md", "rag_source_registry.md", ".gitkeep"}:
            continue
        if path.suffix.lower() != ".md":
            continue
        relative = path.relative_to(source_directory)
        if len(relative.parts) != 2 or relative.parts[0] not in APPROVED_CATEGORIES:
            raise KnowledgeBaseValidationError(f"Markdown document is outside an approved category: {relative}")
        documents.append(path)
    if not documents:
        raise KnowledgeBaseValidationError("No approved Markdown knowledge-base documents were found.")
    return documents


def _source_type(category: str, filename: str) -> str:
    return {
        "clinical_guidelines": "project_policy",
    }.get(category, "synthetic" if filename == "synthetic_emr_structure.md" else "source_backed")


def _source_metadata(content: str, source_type: str) -> tuple[str | None, str | None, str | None]:
    urls = re.findall(r"https?://[^\s)>]+", content)
    organizations = []
    for name in ("Medicare.gov", "CMS", "AHRQ"):
        if re.search(rf"\b{re.escape(name)}\b", content):
            organizations.append(name)
    if source_type == "project_policy":
        organizations = ["Project policy"]
    elif source_type == "project_generated":
        organizations = ["Project-generated"]
    elif source_type == "synthetic" and not organizations:
        organizations = ["Project-defined synthetic content"]
    return ("; ".join(organizations) or None, urls[0] if urls else None, None)


def load_markdown_document(path: Path, source_directory: Path = DEFAULT_SOURCE_DIRECTORY) -> SourceDocument:
    """Read one approved Markdown file and derive provenance without inventing facts."""
    if path.suffix.lower() != ".md":
        raise KnowledgeBaseValidationError(f"Unsupported knowledge-base file type: {path.name}")
    try:
        relative = path.resolve().relative_to(source_directory.resolve())
    except ValueError as error:
        raise KnowledgeBaseValidationError(f"Knowledge-base document is outside the source directory: {path}") from error
    if len(relative.parts) != 2 or relative.parts[0] not in APPROVED_CATEGORIES:
        raise KnowledgeBaseValidationError(f"Knowledge-base document is outside an approved category: {relative}")
    try:
        content = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise KnowledgeBaseValidationError(f"Unable to read knowledge-base document: {path.name}") from error
    if not content:
        raise KnowledgeBaseValidationError(f"Knowledge-base document is empty: {path.name}")
    title_match = re.search(r"^#\s+(.+?)\s*$", content, flags=re.MULTILINE)
    if not title_match:
        raise KnowledgeBaseValidationError(f"Knowledge-base document has no level-one title: {path.name}")
    category = relative.parts[0]
    source_type = _source_type(category, path.name)
    organization, url, version = _source_metadata(content, source_type)
    source_file = relative.as_posix()
    return SourceDocument(
        document_id=f"rag:{category}:{path.stem}", source_file=source_file, category=category,
        title=title_match.group(1).strip(), source_type=source_type,
        source_organization=organization, source_url=url, version=version, content=content,
    )


def load_approved_documents(source_directory: Path = DEFAULT_SOURCE_DIRECTORY) -> list[SourceDocument]:
    return [load_markdown_document(path, source_directory) for path in discover_markdown_documents(source_directory)]


def _sections(document: SourceDocument) -> list[tuple[str, str]]:
    parts = re.split(r"(?=^##\s+)", document.content, flags=re.MULTILINE)
    sections: list[tuple[str, str]] = []
    for part in parts:
        section_title = document.title
        match = re.match(r"^##\s+(.+?)\s*$", part, flags=re.MULTILINE)
        if match:
            section_title = match.group(1).strip()
        text = re.sub(r"\s+", " ", part).strip()
        # The initial split commonly contains only the level-one document
        # title. It is already repeated as context on meaningful chunks, so it
        # must not become a standalone tiny vector. This does not discard a
        # short substantive section or an unsectioned document body.
        if text == f"# {document.title}":
            continue
        if text:
            sections.append((section_title, text))
    return sections


def _split_text(text: str, *, max_size: int = CHUNK_SIZE) -> list[str]:
    if len(text) <= max_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_size, len(text))
        if end < len(text):
            boundary = max(text.rfind(". ", start, end), text.rfind(" ", start, end))
            if boundary > start + max_size // 2:
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def chunk_document(document: SourceDocument) -> list[KnowledgeChunk]:
    """Chunk by section, keeping title/context and a bounded character overlap."""
    chunks: list[KnowledgeChunk] = []
    for section, text in _sections(document):
        context = f"Title: {document.title}\nSection: {section}\n\n"
        # Repeat the compact provenance context in every overlapping chunk.
        # The body budget keeps the resulting record close to CHUNK_SIZE.
        for body in _split_text(text, max_size=max(1, CHUNK_SIZE - len(context))):
            part = context + body
            if not part.strip():
                continue
            ordinal = len(chunks)
            identity = f"{document.document_id}\0{ordinal}\0{part}".encode("utf-8")
            vector_id = "kb-" + hashlib.sha256(identity).hexdigest()
            metadata: dict[str, str | int | None] = {
                "document_id": document.document_id, "source_file": document.source_file,
                "category": document.category, "title": document.title,
                "source_type": document.source_type, "source_organization": document.source_organization,
                "source_url": document.source_url, "version": document.version,
                "chunk_ordinal": ordinal,
            }
            validate_chunk_metadata(metadata)
            chunks.append(KnowledgeChunk(vector_id=vector_id, text=part, metadata=metadata))
    if not chunks:
        raise KnowledgeBaseValidationError(f"Knowledge-base document produced no chunks: {document.source_file}")
    return chunks


def build_knowledge_chunks(source_directory: Path = DEFAULT_SOURCE_DIRECTORY) -> list[KnowledgeChunk]:
    chunks = [chunk for document in load_approved_documents(source_directory) for chunk in chunk_document(document)]
    if not chunks:
        raise KnowledgeBaseValidationError("Knowledge base produced no chunks.")
    return chunks
