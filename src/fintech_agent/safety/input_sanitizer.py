"""Input sanitizer — clean and validate user-provided text.

Prevents injection of control characters and enforces length limits.
Does NOT do PII detection (see pii_masking.py for that).
"""

from __future__ import annotations

import re

# Maximum length for complaint text (characters)
MAX_COMPLAINT_LENGTH = 5000

# Maximum length for single field values
MAX_FIELD_LENGTH = 500


def sanitize_complaint(text: str) -> str:
    """Clean raw complaint text.

    - Strips leading/trailing whitespace
    - Removes control characters (except newlines)
    - Truncates to MAX_COMPLAINT_LENGTH
    """
    # Remove control chars except \n, \r, \t
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    cleaned = cleaned.strip()
    if len(cleaned) > MAX_COMPLAINT_LENGTH:
        cleaned = cleaned[:MAX_COMPLAINT_LENGTH]
    return cleaned


def sanitize_field(value: str) -> str:
    """Clean a single field value (IDs, codes, etc.).

    - Strips whitespace
    - Removes control characters
    - Truncates to MAX_FIELD_LENGTH
    """
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", value)
    cleaned = cleaned.strip()
    if len(cleaned) > MAX_FIELD_LENGTH:
        cleaned = cleaned[:MAX_FIELD_LENGTH]
    return cleaned
