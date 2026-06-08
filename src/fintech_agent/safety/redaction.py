"""Sensitive-data redaction for stored summaries and conversation timelines.

Before ANY customer text is persisted into a back-office ticket (summary or
timeline), it MUST pass through redact_sensitive(). This guarantees PIN / OTP /
password / full card number are never written to storage or shown to staff.

This is storage-side defense-in-depth — separate from the output guardrail,
which protects what is sent back TO the customer.
"""

from __future__ import annotations

import re

_REDACTED = "[đã ẩn]"

# Patterns for sensitive credentials disclosed by the customer.
_REDACTION_PATTERNS: list[re.Pattern] = [
    # Full card number: 16 digits, optionally grouped
    re.compile(r"\b(?:\d[ \-]?){15}\d\b"),
    # PIN / OTP followed by digits, allowing a few words in between:
    # "PIN là 123456", "OTP: 7890", "mã pin 1234", "PIN của tôi là 123456".
    # The digit run (3–8) is the secret and is redacted.
    re.compile(
        r"(?:mã\s+)?(?:PIN|OTP)\b[^\d\n]{0,20}(\d{3,8})",
        re.IGNORECASE,
    ),
    # Password disclosure: "mật khẩu là abc123", "password: x"
    re.compile(r"mật\s+khẩu\s*(?:là|:|=)\s*\S+", re.IGNORECASE),
    re.compile(r"password\s*(?:is|:|=)\s*\S+", re.IGNORECASE),
    # CVV/CVC
    re.compile(r"(?:CVV|CVC)\s*(?:là|:|=)?\s*\d{3,4}", re.IGNORECASE),
]


def redact_sensitive(text: str | None) -> str:
    """Return text with any PIN/OTP/password/card/CVV occurrences redacted.

    Safe to call on any free-text before persisting it.
    """
    if not text:
        return ""
    cleaned = text
    for pattern in _REDACTION_PATTERNS:
        cleaned = pattern.sub(_REDACTED, cleaned)
    return cleaned


def contains_sensitive(text: str | None) -> bool:
    """True if the text appears to contain a disclosed credential."""
    if not text:
        return False
    return any(p.search(text) for p in _REDACTION_PATTERNS)
