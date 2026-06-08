"""LLM-based customer response composer.

Generates specific, reassuring, grounded customer responses using:
  - Message analysis (what did the customer say?)
  - Public-safe evidence (what do we know?)
  - Policy config (what are we allowed to say?)

LLM is used for WORDING ONLY. It must NOT:
  - Decide refund/unlock/force-success/payout
  - Expose internal evidence, fraud scores, tool names
  - Promise actions that haven't been confirmed
  - Invent evidence or SLA that doesn't exist

Deterministic fallback generates from policy YAML templates.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from fintech_agent.llm.message_analyzer import (
    MessageAnalysis,
    load_response_policy,
    get_workflow_policy,
)

logger = logging.getLogger(__name__)


@dataclass
class ComposedResponse:
    """Structured customer response."""
    public_message: str = ""
    tone: str = "calm"  # calm | reassuring | urgent_but_safe
    needs_more_info: bool = False
    safe_missing_info_prompt: str = ""
    safety_reminder_needed: bool = False


# ─── Response Quality Checklist ─────────────────────────────────
# Every response should address as many of these as possible:
# 1. What did we understand from the customer?
# 2. What has been checked or can be checked?
# 3. Where is the likely bottleneck (public-safe)?
# 4. What happens next?
# 5. What does the customer need to provide?
# 6. What does the customer NOT need to provide?
# 7. What should the customer NOT share?
# 8. Can we promise timing? Only if SLA exists in config.


# ─── LLM Composer ──────────────────────────────────────────────

_COMPOSER_SYSTEM_PROMPT = """\
You are a fintech customer support response composer. Write a calm, specific, \
reassuring customer-facing reply in Vietnamese using ONLY the provided \
public-safe diagnosis and active workflow context.

Given:
- The customer's message
- Analysis of their intent and emotion
- The active workflow (authoritative — the reply MUST stay on this topic)
- A public-safe diagnosis (what was checked, confirmed facts, likely issue
  location, customer-safe cause, next step, customer action)
- Resolution status

Write a response that:
1. Acknowledges what the customer said
2. Reflects the diagnosis (confirmed facts, likely issue location, cause)
3. States what happens next
4. If info is needed: asks for it clearly
5. Matches the customer's emotional tone (calm/reassuring/urgent_but_safe)

HARD RULES:
- Do NOT make business decisions.
- Do NOT promise refund, unlock, payout, wallet credit, or an exact ETA unless
  it is explicitly present in the provided diagnosis.
- Stay strictly within the active workflow's topic. Do NOT borrow wording from
  other workflows. In particular, for a non-wallet workflow (e.g. train_ticket,
  utility_bill, fraud_account_lock, merchant_settlement_delay) NEVER use wallet
  balance wording such as "kiểm tra số dư", "ví chưa cập nhật số dư", or
  "cập nhật vào ví" — frame the issue using the diagnosis you were given.
- Do NOT expose internal data, tool names, database fields, rule ids, ledger,
  reconciliation, approval data, risk scores, or fraud flags.
- Never ask for PIN, OTP, password, or full card number.
- Do NOT invent SLA, evidence, or facts not in the diagnosis.
- Keep it concise (3-5 sentences max). Return JSON only.

Return ONLY this JSON:
{
  "public_message": "...",
  "tone": "calm|reassuring|urgent_but_safe",
  "needs_more_info": true/false,
  "safe_missing_info_prompt": "..." or "",
  "safety_reminder_needed": true/false
}"""


def _compose_with_llm(
    customer_message: str,
    analysis: MessageAnalysis,
    active_case_context: dict | None,
    public_safe_evidence: dict,
    resolution_status: str,
) -> ComposedResponse | None:
    """Use LLM to compose a customer response.

    Returns None if LLM is unavailable.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    # Include diagnosis in LLM prompt for grounded response
    diagnosis_for_llm = {
        "what_was_checked": public_safe_evidence.get("what_was_checked", []),
        "confirmed_public_facts": public_safe_evidence.get("confirmed_public_facts", []),
        "customer_safe_cause": public_safe_evidence.get("customer_safe_cause", ""),
        "likely_issue_location": public_safe_evidence.get("likely_issue_location", ""),
        "next_step": public_safe_evidence.get("next_step", ""),
        "customer_action_needed": public_safe_evidence.get("customer_action_needed", ""),
        "confidence": public_safe_evidence.get("confidence", "low"),
    }

    user_prompt = json.dumps({
        "customer_message": customer_message,
        "message_type": analysis.message_type,
        "customer_emotion": analysis.customer_emotion,
        "customer_goal": analysis.customer_goal,
        "workflow": analysis.workflow_hint,
        "resolution_status": resolution_status,
        "diagnosis": diagnosis_for_llm,
        "active_case_workflow": (active_case_context or {}).get("selected_workflow", ""),
    }, ensure_ascii=False)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, timeout=10.0)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _COMPOSER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=512,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        if not raw:
            return None

        parsed = json.loads(raw)

        tone = parsed.get("tone", "calm")
        if tone not in ("calm", "reassuring", "urgent_but_safe"):
            tone = "calm"

        return ComposedResponse(
            public_message=parsed.get("public_message", ""),
            tone=tone,
            needs_more_info=bool(parsed.get("needs_more_info", False)),
            safe_missing_info_prompt=parsed.get("safe_missing_info_prompt", ""),
            safety_reminder_needed=bool(parsed.get("safety_reminder_needed", False)),
        )

    except Exception as exc:
        logger.warning(
            "[Composer] LLM failed (%s): %s — using deterministic fallback",
            type(exc).__name__, exc,
        )
        return None


# ─── Diagnosis-Driven Composer ─────────────────────────────────

def _compose_from_diagnosis(
    public_safe_evidence: dict,
    resolution_status: str,
    safety_reminder: str,
    wf_policy: dict,
    policy: dict | None,
) -> ComposedResponse:
    """Build response from structured diagnosis fields.

    This replaces per-message_type hard-coded templates with a single
    diagnosis-driven builder. The response is assembled from:
      - confirmed_public_facts
      - customer_safe_cause
      - next_step
      - customer_action_needed
    All fields come from the evidence mapper, never hard-coded here.
    """
    cause = public_safe_evidence.get("customer_safe_cause", "")
    facts = public_safe_evidence.get("confirmed_public_facts", [])
    next_step = public_safe_evidence.get("next_step", "")
    action = public_safe_evidence.get("customer_action_needed", "")
    what_we_know = public_safe_evidence.get("what_we_know", "")
    confidence = public_safe_evidence.get("confidence", "low")

    parts: list[str] = []

    # 1. Acknowledge what we found
    if cause:
        parts.append(f"Cảm ơn bạn đã cung cấp thông tin. {cause}")
    elif what_we_know:
        parts.append(f"Cảm ơn bạn đã cung cấp thông tin. {what_we_know}")
    elif facts:
        fact_str = "; ".join(facts)
        parts.append(f"Chúng tôi đã kiểm tra và ghi nhận: {fact_str}.")

    # 2. Next step
    if next_step and resolution_status == "resolved":
        parts.append(f"Hiện tại, {next_step}.")
    elif next_step:
        parts.append(next_step.capitalize() + ".")

    # 2b. Specific public-safe customer action (e.g. verify bank account via
    # official channel). Generic safety-only actions are handled separately.
    if action and any(
        kw in action for kw in ("xác minh", "kênh chính thức")
    ):
        parts.append(action if action.strip().endswith(".") else action.strip() + ".")

    # 3. No match — be honest
    if resolution_status == "no_match" and not cause:
        parts = [
            "Chúng tôi chưa tìm thấy giao dịch phù hợp với thông tin đã cung cấp.",
        ]
        if action:
            parts.append(action.capitalize() + ".")

    # 4. Multiple candidates
    if resolution_status == "multiple_candidates":
        parts = [
            "Chúng tôi tìm thấy nhiều giao dịch có thể phù hợp.",
            "Bạn có thể cho biết thêm mã tham chiếu hoặc thời gian chính xác?",
        ]

    # 5. Safety reminder — skip if a credential warning was already included
    # (e.g. the bank-account-verify action already says "không cung cấp PIN/OTP").
    already_warned = any(
        kw in " ".join(parts).lower()
        for kw in ("mật khẩu", "otp", "mã pin")
    )
    if safety_reminder and not already_warned:
        parts.append(safety_reminder)

    msg = " ".join(parts).strip()

    # Fallback if completely empty
    if not msg:
        guidance = (
            wf_policy.get("guidance_template")
            or (policy or {}).get("generic_guidance_template", "")
        )
        msg = guidance or (
            "Chúng tôi đã ghi nhận thông tin. "
            "Bộ phận hỗ trợ sẽ kiểm tra và phản hồi sớm nhất."
        )
        if safety_reminder:
            msg = f"{msg}\n\n{safety_reminder}"

    needs_info = resolution_status in (
        "need_more_info", "multiple_candidates", "no_match",
    )

    tone = "reassuring" if resolution_status == "resolved" else "calm"

    return ComposedResponse(
        public_message=msg,
        tone=tone,
        needs_more_info=needs_info,
        safety_reminder_needed=bool(safety_reminder),
    )


# ─── Deterministic Fallback ────────────────────────────────────

def _compose_deterministic(
    customer_message: str,
    analysis: MessageAnalysis,
    active_case_context: dict | None,
    public_safe_evidence: dict,
    resolution_status: str,
) -> ComposedResponse:
    """Build response from policy templates when LLM is unavailable."""
    policy = load_response_policy()
    workflow = analysis.workflow_hint if analysis.workflow_hint != "unknown" else ""
    wf_policy = get_workflow_policy(workflow) if workflow else {}

    safety_reminder = (
        wf_policy.get("safety_reminder")
        or policy.get("global_safety_reminder", "")
    )

    # Handle by message_type
    if analysis.message_type == "provide_sensitive_info":
        return ComposedResponse(
            public_message=policy.get("sensitive_info_warning", ""),
            tone="urgent_but_safe",
            needs_more_info=False,
            safety_reminder_needed=True,
        )

    if analysis.message_type == "ask_what_to_do":
        # If we have a specific diagnosis (e.g. bank-account verification needed),
        # answer from it rather than the generic guidance template.
        if public_safe_evidence.get("customer_safe_cause"):
            return _compose_from_diagnosis(
                public_safe_evidence, resolution_status,
                safety_reminder, wf_policy, policy,
            )
        guidance = wf_policy.get("guidance_template") or policy.get("generic_guidance_template", "")
        msg = f"{guidance}\n\n{safety_reminder}".strip()
        return ComposedResponse(
            public_message=msg,
            tone="calm",
            needs_more_info=True,
            safe_missing_info_prompt=guidance,
            safety_reminder_needed=True,
        )

    if analysis.message_type in ("ask_status", "ask_eta"):
        status_msg = wf_policy.get("status_template") or policy.get("status_default_template", "")
        return ComposedResponse(
            public_message=status_msg,
            tone="reassuring",
            needs_more_info=False,
        )

    if analysis.message_type in (
        "provide_missing_info", "follow_up", "ask_where_money_is",
    ):
        return _compose_from_diagnosis(
            public_safe_evidence, resolution_status,
            safety_reminder, wf_policy, policy,
        )

    # new_complaint or unknown: prefer a diagnosis-driven reply when we have one
    # (e.g. merchant settlement diagnosis), so the answer is specific & safe
    # rather than a fixed generic paragraph.
    if public_safe_evidence.get("customer_safe_cause"):
        return _compose_from_diagnosis(
            public_safe_evidence, resolution_status,
            safety_reminder, wf_policy, policy,
        )

    # otherwise → use generic fallback
    fallback_msg = policy.get("generic_fallback_response", "")
    if not fallback_msg:
        fallback_msg = (
            "Chúng tôi đã ghi nhận thông tin của bạn. "
            "Bộ phận hỗ trợ sẽ kiểm tra và phản hồi trong thời gian sớm nhất."
        )

    return ComposedResponse(
        public_message=fallback_msg,
        tone="calm",
        needs_more_info=False,
    )


# ─── Public Entry Point ────────────────────────────────────────

def compose_customer_response(
    customer_message: str,
    message_analysis: MessageAnalysis,
    active_case_context: dict | None = None,
    public_safe_evidence: dict | None = None,
    resolution_status: str = "",
) -> ComposedResponse:
    """Compose a customer-facing response.

    LLM-first with deterministic fallback from policy templates.

    Args:
        customer_message: Original customer message text.
        message_analysis: Output from analyze_customer_message.
        active_case_context: Active case state dict.
        public_safe_evidence: Output from to_public_safe_evidence.
        resolution_status: resolved | multiple_candidates | no_match | etc.

    Returns:
        ComposedResponse with public_message and metadata.
    """
    if public_safe_evidence is None:
        public_safe_evidence = {}

    # Try LLM first
    llm_result = _compose_with_llm(
        customer_message, message_analysis,
        active_case_context, public_safe_evidence,
        resolution_status,
    )

    if llm_result is not None and llm_result.public_message:
        logger.info(
            "[Composer] LLM: tone=%s, needs_info=%s",
            llm_result.tone, llm_result.needs_more_info,
        )
        return llm_result

    # Deterministic fallback
    det_result = _compose_deterministic(
        customer_message, message_analysis,
        active_case_context, public_safe_evidence,
        resolution_status,
    )

    logger.info(
        "[Composer] Deterministic: tone=%s, needs_info=%s, msg_len=%d",
        det_result.tone, det_result.needs_more_info,
        len(det_result.public_message),
    )

    return det_result
