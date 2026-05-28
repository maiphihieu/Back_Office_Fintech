"""Safety guardrails — defense in depth for financial operations.

Modules:
    money_action_guard     — absolute block on forbidden money actions
    input_sanitizer        — clean user input (control chars, length limits)
    pii_masking            — mask PII in logs/audit (phone, email, citizen ID)
    prompt_injection_check — detect injection attempts before LLM calls
"""

from fintech_agent.safety.money_action_guard import (
    FORBIDDEN_ACTIONS,
    SafetyViolation,
    guard_action,
    guard_tool_call,
    is_safe_action,
)

__all__ = [
    "FORBIDDEN_ACTIONS",
    "SafetyViolation",
    "guard_action",
    "guard_tool_call",
    "is_safe_action",
]
