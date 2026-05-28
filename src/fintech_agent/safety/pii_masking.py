"""PII masking — redact sensitive data from logs and audit trails.

Simple regex-based masking for MVP. Masks:
  - Phone numbers (Vietnam format)
  - Email addresses
  - CCCD/CMND (citizen ID numbers)
"""

from __future__ import annotations

import re

# Vietnam phone: 0xxx or +84xxx, 9-11 digits
_PHONE_PATTERN = re.compile(r"(?:\+84|0)\d{8,10}")

# Email
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# CCCD/CMND: 9 or 12 digit numbers (standalone)
_CITIZEN_ID_PATTERN = re.compile(r"\b\d{9}(?:\d{3})?\b")


def mask_pii(text: str) -> str:
    """Mask PII in text for safe logging.

    Order matters: citizen ID first (longer pattern), then phone, then email.

    Args:
        text: Raw text that may contain PII.

    Returns:
        Text with PII replaced by [MASKED_*] tokens.
    """
    result = _CITIZEN_ID_PATTERN.sub("[MASKED_ID]", text)
    result = _PHONE_PATTERN.sub("[MASKED_PHONE]", result)
    result = _EMAIL_PATTERN.sub("[MASKED_EMAIL]", result)
    return result
