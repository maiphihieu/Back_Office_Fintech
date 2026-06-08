"""Follow-up intent analyzer — backward compatibility shim.

This module re-exports from the unified message_analyzer to preserve
backward compatibility with existing code that imports:
  - ActiveCaseContext
  - FollowupAnalysis
  - analyze_customer_followup
  - _TOPUP_GUIDANCE, _WORKFLOW_GUIDANCE, etc.

All logic now lives in message_analyzer.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fintech_agent.llm.message_analyzer import (
    ActiveCaseContext,
    MessageAnalysis,
    analyze_customer_message,
    load_response_policy,
    get_workflow_policy,
)

logger = logging.getLogger(__name__)

# Re-export ActiveCaseContext as-is
__all__ = [
    "ActiveCaseContext",
    "FollowupAnalysis",
    "analyze_customer_followup",
    "_TOPUP_GUIDANCE",
    "_WORKFLOW_GUIDANCE",
]


# ─── Guidance Strings (loaded from policy YAML) ────────────────

def _load_guidance(workflow: str) -> str:
    """Load guidance template from policy YAML."""
    wf_policy = get_workflow_policy(workflow)
    guidance = wf_policy.get("guidance_template", "")
    safety = wf_policy.get("safety_reminder", "")
    if guidance and safety:
        return f"{guidance}\n\n{safety}"
    if guidance:
        return guidance

    # Fallback to global
    policy = load_response_policy()
    generic = policy.get("generic_guidance_template", "")
    global_safety = policy.get("global_safety_reminder", "")
    if generic and global_safety:
        return f"{generic}\n\n{global_safety}"
    return generic or "Vui lòng mô tả thêm chi tiết vấn đề bạn đang gặp."


# Build guidance strings from policy (lazy, cached)
_guidance_cache: dict[str, str] = {}


def _get_guidance(workflow: str) -> str:
    """Get cached guidance for a workflow."""
    if workflow not in _guidance_cache:
        _guidance_cache[workflow] = _load_guidance(workflow)
    return _guidance_cache[workflow]


# Legacy constants — now dynamically loaded but kept for backward compat
@property
def _topup_guidance_prop():
    return _get_guidance("wallet_topup")


# Expose as module-level for backward compat imports
_TOPUP_GUIDANCE = _get_guidance("wallet_topup") if load_response_policy() else (
    "Bạn có thể xem mã giao dịch trong mục Lịch sử giao dịch của ứng dụng ví.\n\n"
    "Nếu chưa tìm thấy, bạn có thể gửi: thời gian giao dịch, số tiền, "
    "ngân hàng đã trừ tiền, mã tham chiếu ngân hàng.\n\n"
    "Vui lòng không gửi mã PIN, OTP hoặc mật khẩu."
)

_WORKFLOW_GUIDANCE: dict[str, str] = {}
_policy = load_response_policy()
if _policy:
    for wf_name in _policy.get("workflows", {}):
        _WORKFLOW_GUIDANCE[wf_name] = _get_guidance(wf_name)


# ─── FollowupAnalysis (legacy dataclass) ────────────────────────

@dataclass
class FollowupAnalysis:
    """Backward-compatible follow-up analysis result.

    New code should use MessageAnalysis from message_analyzer.
    """
    is_followup: bool = False
    intent: str = "unknown"
    belongs_to_active_case: bool = False
    target_workflow: str = ""
    safe_response: str = ""
    confidence: float = 0.0


# ─── Message Type → Intent Mapping ─────────────────────────────

_TYPE_TO_INTENT: dict[str, str] = {
    "follow_up": "follow_up",
    "provide_missing_info": "provide_alternative_transaction_info",
    "ask_status": "ask_status",
    "ask_eta": "ask_status",
    "ask_what_to_do": "ask_what_to_provide",
    "ask_where_money_is": "ask_where_money_is",
    "provide_sensitive_info": "provide_sensitive_info",
    "new_complaint": "new_complaint",
    "unknown": "unknown",
}


def _map_to_followup(
    analysis: MessageAnalysis,
    ctx: ActiveCaseContext,
) -> FollowupAnalysis:
    """Convert new MessageAnalysis to legacy FollowupAnalysis."""
    intent = _TYPE_TO_INTENT.get(analysis.message_type, "unknown")
    wf = analysis.workflow_hint if analysis.workflow_hint != "unknown" else ctx.selected_workflow

    # Build safe_response from policy
    safe_response = ""
    has_active = bool(ctx.case_id)

    if analysis.message_type == "ask_what_to_do":
        if has_active:
            safe_response = _get_guidance(wf)
        else:
            policy = load_response_policy()
            safe_response = policy.get("no_case_greeting", "")

    elif analysis.message_type in ("ask_status", "ask_eta"):
        wf_policy = get_workflow_policy(wf)
        safe_response = wf_policy.get("status_template", "")
        if not safe_response:
            policy = load_response_policy()
            safe_response = policy.get("status_default_template", "")

    elif analysis.message_type == "follow_up" and has_active:
        safe_response = _get_guidance(wf)

    elif analysis.message_type == "provide_sensitive_info":
        policy = load_response_policy()
        safe_response = policy.get("sensitive_info_warning", "")

    is_followup = analysis.message_type not in ("new_complaint", "unknown")

    return FollowupAnalysis(
        is_followup=is_followup and has_active,
        intent=intent,
        belongs_to_active_case=analysis.belongs_to_active_case,
        target_workflow=wf,
        safe_response=safe_response,
        confidence=analysis.confidence,
    )


# ─── Public Entry Point (backward compatible) ──────────────────

def analyze_customer_followup(
    message: str,
    ctx: ActiveCaseContext,
) -> FollowupAnalysis:
    """Analyze a customer follow-up message.

    Backward-compatible wrapper. Internally uses the unified
    analyze_customer_message() and converts to FollowupAnalysis.
    """
    case_context = {
        "selected_workflow": ctx.selected_workflow,
        "service_type": ctx.service_type,
        "awaiting_field": ctx.awaiting_field,
        "has_active_case": bool(ctx.case_id),
    }
    session_context = {"is_authenticated": True}

    analysis = analyze_customer_message(message, case_context, session_context)
    return _map_to_followup(analysis, ctx)
