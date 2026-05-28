"""Main extraction router — selects mock or OpenAI based on MOCK_LLM setting.

Flow:
    MOCK_LLM=true  → mock_extract() → extraction_method="mock_regex"
    MOCK_LLM=false → OpenAI API → parse → extraction_method="openai_llm"
    OpenAI fails   → mock_extract() → extraction_method="fallback_regex"

SAFETY:
    - LLM output is validated through Pydantic (ExtractedInfo).
    - Any unexpected fields from LLM (e.g. "recommended_action",
      "execute_refund") are silently dropped by Pydantic model_validate.
    - amount_claimed is stored for audit only, never used as refund amount.
"""

from __future__ import annotations

import logging

from fintech_agent.config import Settings
from fintech_agent.llm.mock_extractor import mock_extract
from fintech_agent.llm.openai_client import LLMExtractionError, call_openai_extraction
from fintech_agent.schemas.case_state import ExtractedInfo

logger = logging.getLogger(__name__)

# Fields from LLM output that we accept into ExtractedInfo.
# Anything else (e.g. "recommended_action", "execute_refund") is dropped.
_ALLOWED_FIELDS = set(ExtractedInfo.model_fields.keys())


def _sanitize_llm_output(raw: dict) -> dict:
    """Filter LLM output to only allowed ExtractedInfo fields.

    This is a safety layer: even if the LLM hallucinates fields like
    "recommended_action" or "execute_refund", they are silently dropped.

    Args:
        raw: Raw dict from LLM JSON response.

    Returns:
        Dict containing only keys that are valid ExtractedInfo fields.
    """
    return {k: v for k, v in raw.items() if k in _ALLOWED_FIELDS}


def extract_complaint_info(
    complaint: str,
    settings: Settings | None = None,
    user_id: str | None = None,
) -> ExtractedInfo:
    """Extract structured complaint info — the single public API.

    Args:
        complaint: Raw complaint text from customer.
        settings: App settings. If None, loads from environment.
        user_id: Pre-supplied user_id (from state).

    Returns:
        ExtractedInfo with populated fields and extraction_method set.
    """
    if settings is None:
        from fintech_agent.config import get_settings
        settings = get_settings()

    # ── MOCK mode (default) ──────────────────────────────────
    if settings.mock_llm:
        logger.debug("MOCK_LLM=true — using regex extractor")
        return mock_extract(complaint, user_id=user_id)

    # ── REAL OpenAI mode ─────────────────────────────────────
    logger.info("MOCK_LLM=false — calling OpenAI for extraction")
    try:
        raw = call_openai_extraction(complaint, settings)
        sanitized = _sanitize_llm_output(raw)

        # Override extraction_method — LLM cannot set this
        sanitized["extraction_method"] = "openai_llm"

        # If LLM returned a user_id but we already have one from state, prefer state
        if user_id and not sanitized.get("user_id"):
            sanitized["user_id"] = user_id

        # Validate through Pydantic — drops invalid/extra fields, validates types
        extracted = ExtractedInfo.model_validate(sanitized)

        # Compute missing_fields if LLM didn't
        missing: list[str] = []
        if not extracted.transaction_id:
            missing.append("transaction_id")
        if not extracted.user_id:
            missing.append("user_id")
        if not extracted.service_type:
            missing.append("service_type")
        if missing and not extracted.missing_fields:
            extracted.missing_fields = missing

        logger.info(
            "OpenAI extraction successful: txn=%s, service=%s, confidence=%.2f",
            extracted.transaction_id or "MISSING",
            extracted.service_type or "UNKNOWN",
            extracted.confidence or 0.0,
        )
        return extracted

    except (LLMExtractionError, Exception) as exc:
        # Log the failure (not the API key!) and fall back to regex
        if isinstance(exc, LLMExtractionError):
            logger.warning("LLM extraction failed: %s — falling back to regex", exc.reason)
        else:
            logger.warning(
                "Unexpected error in LLM extraction: %s: %s — falling back to regex",
                type(exc).__name__, exc,
            )

        fallback = mock_extract(complaint, user_id=user_id)
        fallback.extraction_method = "fallback_regex"
        return fallback
