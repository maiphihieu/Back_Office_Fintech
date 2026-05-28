"""OpenAI client wrapper for complaint extraction.

Reads config from Settings. Never logs the API key.
Raises LLMExtractionError on any failure (timeout, bad JSON, API error).
"""

from __future__ import annotations

import json
import logging

from fintech_agent.config import Settings
from fintech_agent.llm.prompts import build_extraction_messages

logger = logging.getLogger(__name__)


class LLMExtractionError(Exception):
    """Raised when OpenAI extraction fails for any reason.

    Callers should catch this and fall back to regex extraction.
    """

    def __init__(self, reason: str, original_error: Exception | None = None) -> None:
        self.reason = reason
        self.original_error = original_error
        super().__init__(f"LLM extraction failed: {reason}")


def call_openai_extraction(complaint: str, settings: Settings) -> dict:
    """Call OpenAI API to extract structured info from a complaint.

    Args:
        complaint: Raw complaint text.
        settings: Application settings (contains API key, model, timeout).

    Returns:
        Parsed dict from the LLM JSON response.

    Raises:
        LLMExtractionError: On missing API key, API error, timeout, or invalid JSON.
    """
    if not settings.openai_api_key:
        raise LLMExtractionError("OPENAI_API_KEY is not set. Set it in .env or use MOCK_LLM=true.")

    # Import openai lazily so MOCK_LLM=true never requires the package at import time
    try:
        from openai import OpenAI, APIError, APITimeoutError
    except ImportError as exc:
        raise LLMExtractionError(
            "openai package not installed. Run: pip install openai>=1.30.0",
            original_error=exc,
        )

    # SAFETY: Never log the API key
    logger.info(
        "Calling OpenAI extraction: model=%s, timeout=%ds, complaint_len=%d",
        settings.openai_model,
        settings.llm_timeout,
        len(complaint),
    )

    try:
        client = OpenAI(
            api_key=settings.openai_api_key,
            timeout=float(settings.llm_timeout),
        )

        messages = build_extraction_messages(complaint)

        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            temperature=0.0,  # deterministic extraction
            max_tokens=1024,
            response_format={"type": "json_object"},
        )

        raw_content = response.choices[0].message.content
        if not raw_content:
            raise LLMExtractionError("OpenAI returned empty response content")

        logger.debug("OpenAI raw response length: %d", len(raw_content))

    except APITimeoutError as exc:
        raise LLMExtractionError(
            f"OpenAI API timeout after {settings.llm_timeout}s",
            original_error=exc,
        )
    except APIError as exc:
        raise LLMExtractionError(
            f"OpenAI API error: {exc.message}",
            original_error=exc,
        )
    except LLMExtractionError:
        raise
    except Exception as exc:
        raise LLMExtractionError(
            f"Unexpected error calling OpenAI: {type(exc).__name__}: {exc}",
            original_error=exc,
        )

    # Parse JSON
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise LLMExtractionError(
            f"OpenAI returned invalid JSON: {exc}",
            original_error=exc,
        )

    if not isinstance(parsed, dict):
        raise LLMExtractionError(f"Expected JSON object, got {type(parsed).__name__}")

    return parsed
