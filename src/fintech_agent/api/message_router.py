"""Generic message router — classifies customer messages without
coupling to any specific workflow.

Wraps ``message_analyzer.analyze_customer_message()`` into the standard
``RouterResult`` contract used by the chatbot pipeline. Adds expanded
message types (ask_recheck, customer_disagrees, etc.) and scope detection.

IMPORTANT: The router does NOT make business decisions. It classifies
the customer's intent, emotion, and workflow hint — nothing more.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Expanded message types recognized by the framework
EXPANDED_MESSAGE_TYPES = frozenset({
    # Original types (from message_analyzer)
    "new_complaint",
    "follow_up",
    "provide_missing_info",
    "provide_sensitive_info",
    "correct_previous_info",
    "ask_what_to_do",
    "ask_status",
    "ask_eta",
    "ask_where_money_is",
    "greeting",
    "thank_you",
    "unknown",
    # Expanded types
    "ask_recheck",           # "kiểm tra lại đi", "check lại"
    "ask_account_status",    # "tài khoản tôi bị gì?", "tài khoản đang sao?"
    "customer_disagrees",    # "không phải", "sai rồi", "tôi không đồng ý"
    "customer_frustrated",   # "quá lâu rồi", "tức quá", "vô lý"
    "ask_staff_support",     # "cho tôi gặp nhân viên", "nối máy"
    "faq",                   # "phí nạp bao nhiêu?", "cách nạp tiền?"
    "workflow_switch",       # switching complaint topic mid-conversation
    "acknowledgement",       # "được rồi", "ok", "vâng" — short confirmations
    "close_conversation",    # "tôi không cần nữa", "thôi khỏi"
    "continue_waiting",      # "tôi sẽ chờ", "tôi đợi nhé"
})


@dataclass
class RouterResult:
    """Output of the generic message router.

    The chatbot pipeline reads these fields — never the raw LLM analysis
    directly.
    """

    scope: str = "in_scope"
    # in_scope | off_topic | unsafe_sensitive_info | unknown

    message_type: str = "unknown"
    workflow_hint: str = "unknown"
    belongs_to_active_case: bool = False

    customer_claims: dict[str, Any] = field(default_factory=dict)
    customer_emotion: str = "neutral"
    customer_goal: str = ""

    # Is this a correction of previous info?
    is_correction: bool = False

    # Raw analysis (for callers that need full access)
    _raw_analysis: Any = field(default=None, repr=False)


# ─── Recheck / expanded type detection ─────────────────────────

_RECHECK_KEYWORDS = (
    "kiểm tra lại", "check lại", "xem lại",
    "chưa được", "vẫn chưa", "kiểm tra giúp lại",
    "thử lại", "recheck",
)

_ACCOUNT_STATUS_KEYWORDS = (
    "tài khoản tôi bị gì", "tài khoản đang sao", "tại sao bị khóa",
    "lý do khóa", "sao bị khóa", "account status",
)

_DISAGREE_KEYWORDS = (
    "không phải", "sai rồi", "tôi không đồng ý", "bạn sai",
    "không đúng", "nhầm rồi",
)

_FRUSTRATED_KEYWORDS = (
    "quá lâu", "tức quá", "vô lý", "không chấp nhận",
    "mệt quá", "tệ quá", "terrible",
)

_STAFF_KEYWORDS = (
    "gặp nhân viên", "nối máy", "chuyển nhân viên",
    "human agent", "speak to agent", "talk to agent",
)


def _detect_expanded_type(msg_lower: str, base_type: str) -> str:
    """Upgrade base message_type to an expanded type if keyword match."""
    if base_type == "provide_sensitive_info":
        return base_type  # Never override safety detection

    if any(kw in msg_lower for kw in _RECHECK_KEYWORDS):
        return "ask_recheck"
    if any(kw in msg_lower for kw in _ACCOUNT_STATUS_KEYWORDS):
        return "ask_account_status"
    if any(kw in msg_lower for kw in _DISAGREE_KEYWORDS):
        return "customer_disagrees"
    if any(kw in msg_lower for kw in _FRUSTRATED_KEYWORDS):
        return "customer_frustrated"
    if any(kw in msg_lower for kw in _STAFF_KEYWORDS):
        return "ask_staff_support"

    return base_type


# ─── Main Router ───────────────────────────────────────────────

def route_customer_message(
    session: dict[str, Any] | None,
    conversation_state: Any | None,
    latest_message: str,
    active_case_context: dict[str, Any] | None = None,
) -> RouterResult:
    """Route a customer message through the message analyzer and return
    a standardised ``RouterResult``.

    This function:
      1. Calls ``analyze_customer_message()`` with appropriate context.
      2. Detects expanded message types (ask_recheck, etc.).
      3. Detects scope (in_scope / unsafe / off_topic).
      4. Returns a ``RouterResult`` ready for the chatbot pipeline.

    Args:
        session: Authenticated session dict (user_id, subject_type, etc.).
        conversation_state: Current ``ConversationState`` (or None).
        latest_message: Raw customer message text.
        active_case_context: Legacy active case dict (for backward compat).

    Returns:
        RouterResult with scope, message_type, workflow_hint, claims, etc.
    """
    from fintech_agent.llm.message_analyzer import analyze_customer_message

    # Build context dict for the analyzer
    case_ctx = active_case_context
    if case_ctx is None and conversation_state is not None:
        if hasattr(conversation_state, "to_active_case_context"):
            case_ctx = conversation_state.to_active_case_context()

    # Run the analyzer
    analysis = analyze_customer_message(latest_message, case_ctx)

    # Extract base info
    base_type = analysis.message_type
    workflow_hint = analysis.workflow_hint or "unknown"
    belongs_to_active = analysis.belongs_to_active_case

    # Expand message type
    msg_lower = latest_message.lower().strip()
    expanded_type = _detect_expanded_type(msg_lower, base_type)

    # Detect scope
    if expanded_type == "provide_sensitive_info":
        scope = "unsafe_sensitive_info"
    elif expanded_type in ("greeting", "thank_you"):
        scope = "in_scope"  # Polite messages are always in-scope
    elif workflow_hint == "unknown" and not belongs_to_active:
        scope = "unknown"  # Can't determine scope yet
    else:
        scope = "in_scope"

    # Build claims from extracted fields
    claims: dict[str, Any] = {}
    if hasattr(analysis, "extracted") and analysis.extracted:
        extracted = analysis.extracted
        for attr in (
            "transaction_id", "order_id", "bill_code", "customer_code",
            "amount", "bank_name", "bank_reference",
            "approximate_time_text", "approximate_date_text",
            "service_type", "issue_type",
            "payout_id", "batch_id", "settlement_date",
            "merchant_id", "merchant_name", "tax_code",
            "phone", "email",
        ):
            val = getattr(extracted, attr, None)
            if val is not None and val != "" and val != "unknown":
                claims[attr] = val

    # Detect correction
    is_correction = (
        expanded_type == "correct_previous_info"
        or base_type == "correct_previous_info"
        or getattr(analysis, "is_correction", False)
    )

    return RouterResult(
        scope=scope,
        message_type=expanded_type,
        workflow_hint=workflow_hint,
        belongs_to_active_case=belongs_to_active,
        customer_claims=claims,
        customer_emotion=getattr(analysis, "customer_emotion", "neutral") or "neutral",
        customer_goal=getattr(analysis, "customer_goal", "") or "",
        is_correction=is_correction,
        _raw_analysis=analysis,
    )
