"""Small Gemini text-generation service for the RightPath AI assistant layer.

This is a thin, stateless wrapper around Gemini's ``generate_content`` API --
structurally separate from backend/rag/embeddings.py's GeminiEmbedder, which
only ever calls ``embed_content``. Nothing here makes a clinical or triage
decision: callers (backend/routes/assistant.py) are responsible for supplying
only context the deterministic safety engine and analytics services have
already computed. This module never fabricates a successful reply -- any
failure (missing config, network error, empty response) raises
AssistantGenerationError for the caller to turn into a controlled error.
"""

from __future__ import annotations


class AssistantGenerationError(RuntimeError):
    """Raised when Gemini text generation fails, is misconfigured, or the
    response cannot be trusted as a real answer."""


class GeminiAssistant:
    """Generates one plain-text reply from a system instruction + user content."""

    def __init__(self, *, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def generate(self, *, system_instruction: str, user_content: str) -> str:
        if not isinstance(user_content, str) or not user_content.strip():
            raise AssistantGenerationError("user_content must be a non-empty string.")
        if not isinstance(system_instruction, str) or not system_instruction.strip():
            raise AssistantGenerationError("system_instruction must be a non-empty string.")

        try:
            from google import genai
            from google.genai import types
        except ImportError as error:
            raise AssistantGenerationError(
                "google-genai is required for the AI assistant; install requirements.txt first."
            ) from error

        try:
            client = genai.Client(api_key=self.api_key)
            response = client.models.generate_content(
                model=self.model,
                contents=user_content,
                config=types.GenerateContentConfig(system_instruction=system_instruction),
            )
        except Exception as error:
            raise AssistantGenerationError("Gemini text generation failed.") from error

        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise AssistantGenerationError("Gemini returned an empty response.")
        return text.strip()
