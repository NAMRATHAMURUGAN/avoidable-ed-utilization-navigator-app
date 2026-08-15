"""Run the explicit offline RAG knowledge-base ingestion operation."""

from __future__ import annotations

import sys
from pathlib import Path

# Match the repository's documented ``python backend/<script>.py`` command.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.rag.ingestion import ingest_knowledge_base


if __name__ == "__main__":
    result = ingest_knowledge_base()
    print(
        "RAG knowledge-base ingestion complete: "
        f"documents={result.document_count}, chunks={result.chunk_count}, upserted={result.upserted_count}"
    )
