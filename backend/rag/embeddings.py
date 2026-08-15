"""Google GenAI embedding adapter; no generation or retrieval lives here."""

from __future__ import annotations

from typing import Sequence


class EmbeddingValidationError(ValueError):
    """Raised when an embedding response cannot safely be indexed."""


class GeminiEmbedder:
    def __init__(self, *, api_key: str, model: str, dimension: int) -> None:
        self.api_key = api_key
        self.model = model
        self.dimension = dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts or any(not text.strip() for text in texts):
            raise EmbeddingValidationError("Embedding input must contain non-empty text.")
        try:
            from google import genai
            from google.genai import types
        except ImportError as error:
            raise RuntimeError("google-genai is required for RAG ingestion; install requirements.txt first.") from error
        client = genai.Client(api_key=self.api_key)
        response = client.models.embed_content(
            model=self.model,
            contents=list(texts),
            config=types.EmbedContentConfig(output_dimensionality=self.dimension),
        )
        vectors = [list(embedding.values) for embedding in response.embeddings]
        validate_embeddings(vectors, self.dimension, expected_count=len(texts))
        return vectors


def validate_embeddings(vectors: Sequence[Sequence[float]], dimension: int, *, expected_count: int | None = None) -> None:
    if not vectors:
        raise EmbeddingValidationError("Embedding response contained no vectors.")
    if expected_count is not None and len(vectors) != expected_count:
        raise EmbeddingValidationError("Embedding response count does not match the input chunk count.")
    for position, vector in enumerate(vectors):
        if len(vector) != dimension:
            raise EmbeddingValidationError(
                f"Embedding {position} has dimension {len(vector)}; expected {dimension}."
            )
