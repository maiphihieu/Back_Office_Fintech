"""Prompt injection detection — basic checks for LLM input safety.

Detects common prompt injection patterns before text is sent to the LLM.
This is a defense-in-depth measure — even if injection succeeds,
the rule engine (not LLM) makes all financial decisions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class InjectionCheckResult:
    """Result of prompt injection check."""

    is_suspicious: bool
    reason: str


# Patterns that suggest prompt injection attempts
_INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
     "ignore_previous_instructions"),
    (re.compile(r"you\s+are\s+now\s+(?:a|an)\s+", re.IGNORECASE),
     "role_override_attempt"),
    (re.compile(r"system\s*:\s*", re.IGNORECASE),
     "system_prompt_injection"),
    (re.compile(r"<\|(?:system|im_start|im_end)\|>", re.IGNORECASE),
     "special_token_injection"),
    (re.compile(r"execute_refund|update_wallet|edit_ledger|mark_payment", re.IGNORECASE),
     "forbidden_action_in_text"),
]


def check_prompt_injection(text: str) -> InjectionCheckResult:
    """Check text for common prompt injection patterns.

    Args:
        text: The text to check (usually complaint text).

    Returns:
        InjectionCheckResult. If suspicious, the text should be flagged
        but NOT blocked — humans need to review it.
    """
    for pattern, reason in _INJECTION_PATTERNS:
        if pattern.search(text):
            return InjectionCheckResult(
                is_suspicious=True,
                reason=reason,
            )
    return InjectionCheckResult(is_suspicious=False, reason="clean")
