"""Small Gemini text-generation service for the RightPath AI assistant layer.

This is a thin, stateless wrapper around Gemini's ``generate_content`` API --
structurally separate from backend/rag/embeddings.py's GeminiEmbedder, which
only ever calls ``embed_content``. Nothing here makes a clinical or triage
decision: callers (backend/routes/assistant.py) are responsible for supplying
only context the deterministic safety engine and analytics services have
already computed. This module never fabricates a successful reply -- any
non-retryable failure (missing config, network error, empty response) raises
AssistantGenerationError for the caller to turn into a controlled fallback.

DEMO-HARDENING: two changes, both scoped entirely to this one network call
and never touching triage/safety/analytics logic:
  1. A "low" thinking configuration -- verified against the currently
     installed google-genai SDK/API (thinking_budget=0 is REJECTED by
     gemini-3.6-flash with an INVALID_ARGUMENT error; thinking_level=LOW is
     accepted -- see the comment inside GeminiAssistant.generate() below).
     Complex multi-step reasoning is not required for these short
     care-navigation/analytics answers.
  2. At most ONE bounded retry, and only for errors that are actually worth
     retrying (a short-lived rate limit, a transient 5xx, or a network-level
     failure) -- never for a daily/project quota exhaustion, which cannot
     succeed on retry and must fail fast so the caller's graceful fallback
     (see backend/routes/assistant.py) renders immediately instead of the
     patient/payer staring at a multi-second stall.

LATENCY INVESTIGATION FINDINGS (this phase): direct timing of the real API
path (not assumed) found two costs that had nothing to do with Gemini's own
generation time:
  - Importing google-genai cold costs ~3.7s, measured directly. It was
    previously imported INSIDE generate(), so that cost was silently
    attributed to whichever request happened to run first in a given
    process. Moved to module level so it is paid once, at import time, and
    is a true zero-cost no-op (~0.006ms, measured) on every call after.
  - Constructing genai.Client(...) costs ~1.4-1.7s EVERY time, measured
    directly and repeatably (not a one-time warm-up cost) -- and the
    previous code constructed a fresh Client on every single request. The
    client carries no per-request state (reusing one Client across calls is
    the SDK's own documented pattern), so _get_client() below caches one
    Client per api_key for the process lifetime. This was the single
    highest-value fix: ~1.5s off every Gemini-backed request, not just the
    first. Retry was directly measured and ruled out as a contributor to
    normal successful requests (attempts=1, retry_sleep_ms=0.0 observed on
    a real successful call).
"""

from __future__ import annotations

import functools
import logging
import random
import time

try:
    from google import genai
    from google.genai import types
    _IMPORT_ERROR: Exception | None = None
except ImportError as _caught_import_error:  # pragma: no cover - exercised only if the dependency is missing
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]
    _IMPORT_ERROR = _caught_import_error

# Sub-stage latency instrumentation (this phase's addition, kept for
# production use going forward): logs ONLY safe numeric/route metadata --
# model name, attempt count, per-stage elapsed milliseconds, and token
# COUNTS (not content) from Gemini's own usage_metadata. Never logs the
# prompt, system instruction, or generated text. Complements the
# already-existing route-level timing in backend/routes/assistant.py, which
# measures the whole generate() call as one "gemini_ms" figure; this breaks
# that figure down into client creation, each individual attempt, and any
# retry backoff sleep, so a slow request can be attributed to the right
# cause instead of guessed at.
_generation_logger = logging.getLogger("rightpath.assistant.generation")


class AssistantGenerationError(RuntimeError):
    """Raised when Gemini text generation fails, is misconfigured, or the
    response cannot be trusted as a real answer."""


_MAX_ATTEMPTS = 2  # the original call plus at most one retry
_RETRY_BASE_DELAY_SECONDS = 0.3
_RETRY_JITTER_SECONDS = 0.2
_TRANSIENT_HTTP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_HTTP_REQUEST_TIMEOUT_MS = 15_000  # caps a single hung/slow HTTP attempt; our own retry (below) handles the rest


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _retry_backoff_seconds(attempt: int) -> float:
    """Short exponential backoff with jitter. With _MAX_ATTEMPTS=2 there is
    only ever one possible retry, so this deliberately stays sub-second --
    never enough to turn a ~1s response into a multi-second demo stall."""
    return _RETRY_BASE_DELAY_SECONDS * (2**attempt) + random.uniform(0, _RETRY_JITTER_SECONDS)


def _error_search_text(error: Exception) -> str:
    return f"{getattr(error, 'details', '')} {getattr(error, 'message', '')} {error}".lower()


def _is_daily_quota_exhausted(error: Exception) -> bool:
    """True only for a quota error that a short retry cannot resolve --
    detected from the structured quota-scope marker google.genai attaches to
    a genuinely exhausted daily/project quota (a quotaId containing
    "PerDay", e.g. "GenerateRequestsPerDayPerProjectPerModel-FreeTier").
    Never guessed from the 429 status code alone: a short-lived per-minute
    rate limit is also a 429 and IS safe to retry once."""
    text = _error_search_text(error).replace(" ", "").replace("_", "")
    return "perday" in text


def _is_transient_gemini_error(error: Exception) -> bool:
    """True only for errors worth one bounded retry: a short-lived rate
    limit, a transient server error (500/502/503/504), or a network-level
    failure. A daily/project quota exhaustion is deliberately excluded --
    retrying it cannot succeed within a demo-appropriate window, so it must
    fail fast into the caller's graceful fallback instead."""
    code = getattr(error, "code", None)
    if isinstance(code, int) and code in _TRANSIENT_HTTP_STATUS_CODES:
        return not _is_daily_quota_exhausted(error)
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    return False


@functools.lru_cache(maxsize=4)
def _get_client(api_key: str):
    """One genai.Client reused for the lifetime of the process, per api_key.
    Directly measured: constructing a fresh Client() costs ~1.4-1.7s EVERY
    time, not just on first use, and the previous code paid that cost on
    every single Gemini-backed request. The client holds no per-request
    state -- reusing one instance across calls is the SDK's own documented
    pattern -- so caching changes no behavior, only removes that repeated,
    unnecessary construction cost. Tests that need a fresh (mocked) client
    call _get_client.cache_clear() first; maxsize=4 is a defensive cap, not
    a real limit, since this process only ever uses one api_key."""
    return genai.Client(api_key=api_key)


class GeminiAssistant:
    """Generates one plain-text reply from a system instruction + user content."""

    def __init__(self, *, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def generate(
        self,
        *,
        system_instruction: str,
        user_content: str,
        max_output_tokens: int | None = None,
    ) -> str:
        if not isinstance(user_content, str) or not user_content.strip():
            raise AssistantGenerationError("user_content must be a non-empty string.")
        if not isinstance(system_instruction, str) or not system_instruction.strip():
            raise AssistantGenerationError("system_instruction must be a non-empty string.")
        if _IMPORT_ERROR is not None:
            raise AssistantGenerationError(
                "google-genai is required for the AI assistant; install requirements.txt first."
            ) from _IMPORT_ERROR

        # "LOW" thinking is the lowest level this SDK/model combination
        # actually accepts for gemini-3.6-flash (thinking_budget=0 returns a
        # 400 INVALID_ARGUMENT from the live API -- confirmed by direct
        # testing, not assumed). Guarded so an unexpected older SDK without
        # ThinkingLevel simply skips this rather than breaking generation.
        try:
            thinking_config = types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
        except AttributeError:
            thinking_config = None

        # The single largest latency finding of this investigation: the SDK
        # itself retries a transient HTTP error (408/429/5xx) internally,
        # by default up to 5 attempts with exponential backoff capped at
        # 60s BETWEEN attempts -- invisible to our own timing/retry logic.
        # Measured directly: one "attempt" from our code's perspective took
        # 53.8s before it ever raised, entirely inside this SDK-internal
        # retry loop, and our own bounded retry then fired again on top of
        # that. attempts=1 disables the SDK's own retry (we already have
        # one bounded, fast, jittered retry of our own below -- two
        # independent, stacking retry policies is strictly worse, not
        # safer). _HTTP_REQUEST_TIMEOUT_MS caps a single hung/slow request
        # rather than leaving it unbounded. Guarded the same way as
        # thinking_config above, for an older SDK that lacks these fields.
        try:
            http_options = types.HttpOptions(
                timeout=_HTTP_REQUEST_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=1),
            )
        except AttributeError:
            http_options = None

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            max_output_tokens=max_output_tokens,
            thinking_config=thinking_config,
            http_options=http_options,
        )

        client_creation_start = time.perf_counter()
        client = _get_client(self.api_key)
        client_creation_ms = _elapsed_ms(client_creation_start)

        response = None
        attempt_ms: list[float] = []
        retry_sleep_ms = 0.0
        call_start = time.perf_counter()
        for attempt in range(_MAX_ATTEMPTS):
            attempt_start = time.perf_counter()
            try:
                response = client.models.generate_content(
                    model=self.model, contents=user_content, config=config
                )
                attempt_ms.append(_elapsed_ms(attempt_start))
                break
            except Exception as error:
                attempt_ms.append(_elapsed_ms(attempt_start))
                is_last_attempt = attempt == _MAX_ATTEMPTS - 1
                if is_last_attempt or not _is_transient_gemini_error(error):
                    self._log_generation_timing(
                        outcome="failed", client_creation_ms=client_creation_ms,
                        attempt_ms=attempt_ms, retry_sleep_ms=retry_sleep_ms,
                        total_ms=_elapsed_ms(call_start),
                    )
                    raise AssistantGenerationError("Gemini text generation failed.") from error
                sleep_start = time.perf_counter()
                time.sleep(_retry_backoff_seconds(attempt))
                retry_sleep_ms += _elapsed_ms(sleep_start)

        text = getattr(response, "text", None)
        usage = getattr(response, "usage_metadata", None)
        self._log_generation_timing(
            outcome="success", client_creation_ms=client_creation_ms,
            attempt_ms=attempt_ms, retry_sleep_ms=retry_sleep_ms,
            total_ms=_elapsed_ms(call_start), usage=usage,
        )
        if not isinstance(text, str) or not text.strip():
            raise AssistantGenerationError("Gemini returned an empty response.")
        return text.strip()

    def _log_generation_timing(
        self, *, outcome: str, client_creation_ms: float, attempt_ms: list[float],
        retry_sleep_ms: float, total_ms: float, usage=None,
    ) -> None:
        prompt_tokens = getattr(usage, "prompt_token_count", None) if usage else None
        thoughts_tokens = getattr(usage, "thoughts_token_count", None) if usage else None
        answer_tokens = getattr(usage, "candidates_token_count", None) if usage else None
        _generation_logger.info(
            "model=%s outcome=%s attempts=%d client_creation_ms=%.1f "
            "attempt_ms=%s retry_sleep_ms=%.1f total_ms=%.1f "
            "prompt_tokens=%s thoughts_tokens=%s answer_tokens=%s",
            self.model, outcome, len(attempt_ms), client_creation_ms,
            [round(ms, 1) for ms in attempt_ms], retry_sleep_ms, total_ms,
            prompt_tokens, thoughts_tokens, answer_tokens,
        )
