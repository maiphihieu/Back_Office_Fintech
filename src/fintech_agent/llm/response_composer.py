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
import re
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
- customer_claims: what the customer SAID — NOT verified. Treat as unverified input.
- verified_evidence: what the system CONFIRMED from database/tools. Only this is factual.
- Contradictions (mismatches between claims and evidence)

SOURCE LABELING (MANDATORY):
- When referring to information from customer_claims, prefix with "bạn cung cấp" \
  (e.g., "bạn cung cấp số tiền 500.000đ").
- When referring to information from verified_evidence, prefix with \
  "theo kiểm tra hệ thống" (e.g., "theo kiểm tra hệ thống, giao dịch ghi nhận 500.000đ").
- If the customer's claimed value matches the verified value exactly, you may \
  omit the claim echo and only state the verified fact with the "theo kiểm tra \
  hệ thống" prefix.
- NEVER present customer-provided data as if the system confirmed it.

Write a response that:
1. Acknowledges what the customer said (label as "bạn cung cấp")
2. Reflects the diagnosis using verified_evidence (label as "theo kiểm tra hệ thống")
3. States what happens next
4. If info is needed: asks for it clearly
5. Matches the customer's emotional tone (calm/reassuring/urgent_but_safe)
6. If contradictions exist: politely note the discrepancy WITHOUT accusing.
   Example: "Theo kiểm tra hệ thống, giao dịch ghi nhận số tiền 300.000đ \
   (bạn cung cấp 500.000đ). Bạn vui lòng xác nhận lại số tiền chính xác?"

HARD RULES:
- Do NOT make business decisions.
- Do NOT promise refund, unlock, payout, wallet credit, or an exact ETA unless
  it is explicitly present in the provided diagnosis.
- Do NOT say "đã xác nhận" or "đã kiểm tra" for customer-claimed info that has
  NOT been verified by the system. Only use confirmed language for fields in
  the verified_evidence section.
- Distinguish the bank/payment side from the customer's wallet/account credit.
  Only state that the customer's wallet/account has RECEIVED the money, or that
  the transaction SUCCEEDED/COMPLETED, if that exact fact is present in
  confirmed_public_facts. If the bank side is confirmed but wallet credit is not
  a confirmed fact (i.e. still pending/being processed), you MUST say the money
  has NOT yet been credited to the customer's wallet and is being processed —
  never imply otherwise. Confirming a credit that has not happened is a serious
  error.
- Stay strictly within the active workflow's topic. Do NOT borrow wording from
  other workflows. In particular, for a non-wallet workflow (e.g. train_ticket,
  utility_bill, fraud_account_lock, merchant_settlement_delay) NEVER use wallet
  balance wording such as "kiểm tra số dư", "ví chưa cập nhật số dư", or
  "cập nhật vào ví" — frame the issue using the diagnosis you were given.
- Do NOT expose internal data, tool names, database fields, rule ids, ledger,
  reconciliation, approval data, risk scores, or fraud flags.
- Never ask for PIN, OTP, password, or full card number.
- Do NOT invent SLA, evidence, or facts not in the diagnosis.
- Keep it concise: 2-5 sentences ONLY. Return JSON only.

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
    contradictions: list | None = None,
    recheck_context: dict | None = None,
    customer_claims: dict | None = None,
    verified_evidence: dict | None = None,
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

    user_prompt_data = {
        "customer_message": customer_message,
        "message_type": analysis.message_type,
        "customer_emotion": analysis.customer_emotion,
        "customer_goal": analysis.customer_goal,
        "workflow": analysis.workflow_hint,
        "resolution_status": resolution_status,
        "diagnosis": diagnosis_for_llm,
        "active_case_workflow": (active_case_context or {}).get("selected_workflow", ""),
    }

    # Pass customer claims and verified evidence as separate sections
    # so the LLM can properly label sources in the response.
    if customer_claims:
        user_prompt_data["customer_claims"] = customer_claims
    if verified_evidence:
        user_prompt_data["verified_evidence"] = verified_evidence

    # Include contradictions if any
    if contradictions:
        user_prompt_data["contradictions"] = [
            {
                "field": c.get("field", "") if isinstance(c, dict) else getattr(c, "field", ""),
                "customer_claim": c.get("customer_claim") if isinstance(c, dict) else getattr(c, "customer_claim", None),
                "verified_value": c.get("verified_value") if isinstance(c, dict) else getattr(c, "verified_value", None),
            }
            for c in contradictions
        ]

    # Include recheck context if this is a status recheck
    if recheck_context:
        user_prompt_data["recheck_context"] = recheck_context

    user_prompt = json.dumps(user_prompt_data, ensure_ascii=False)

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
    contradictions: list | None = None,
    recheck_context: dict | None = None,
) -> ComposedResponse:
    """Build response from structured diagnosis fields.

    This replaces per-message_type hard-coded templates with a single
    diagnosis-driven builder. The response is assembled from:
      - confirmed_public_facts
      - customer_safe_cause
      - next_step
      - customer_action_needed
    All fields come from the evidence mapper, never hard-coded here.

    If contradictions exist, prepends a polite correction before the diagnosis.
    """
    cause = public_safe_evidence.get("customer_safe_cause", "")
    facts = public_safe_evidence.get("confirmed_public_facts", [])
    next_step = public_safe_evidence.get("next_step", "")
    action = public_safe_evidence.get("customer_action_needed", "")
    what_we_know = public_safe_evidence.get("what_we_know", "")
    confidence = public_safe_evidence.get("confidence", "low")

    parts: list[str] = []

    # 0a. Recheck notice — tell the customer whether the status changed
    if recheck_context and recheck_context.get("is_recheck"):
        if recheck_context.get("status_changed"):
            parts.append(
                "Hệ thống vừa kiểm tra lại. Trạng thái đã được cập nhật."
            )
        else:
            parts.append(
                "Hệ thống vừa kiểm tra lại — trạng thái hiện tại vẫn giữ nguyên."
            )

    # 0b. Contradiction notice (polite, non-accusatory)
    if contradictions:
        for c in contradictions:
            field = c.get("field", "") if isinstance(c, dict) else getattr(c, "field", "")
            claim = c.get("customer_claim") if isinstance(c, dict) else getattr(c, "customer_claim", None)
            verified = c.get("verified_value") if isinstance(c, dict) else getattr(c, "verified_value", None)
            if field == "amount" and claim is not None and verified is not None:
                try:
                    claim_fmt = f"{int(claim):,}".replace(",", ".")
                    verified_fmt = f"{int(verified):,}".replace(",", ".")
                    parts.append(
                        f"Theo hệ thống, giao dịch ghi nhận số tiền {verified_fmt}đ "
                        f"(bạn đã đề cập {claim_fmt}đ). "
                        f"Bạn vui lòng xác nhận lại số tiền chính xác."
                    )
                except (ValueError, TypeError):
                    pass
            elif field == "bank_name" and claim and verified:
                parts.append(
                    f"Theo hệ thống, ngân hàng ghi nhận là {verified} "
                    f"(bạn đã đề cập {claim}). "
                    f"Bạn vui lòng xác nhận lại thông tin."
                )

    # 1. Acknowledge what we found — prefix verified data with source label
    if cause:
        parts.append(f"Theo kiểm tra hệ thống, {cause[0].lower()}{cause[1:]}")
    elif what_we_know:
        parts.append(f"Theo kiểm tra hệ thống, {what_we_know[0].lower()}{what_we_know[1:]}")
    elif facts:
        fact_str = "; ".join(facts)
        parts.append(f"Theo kiểm tra hệ thống: {fact_str}.")

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
    ) or bool(contradictions)

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
    contradictions: list | None = None,
    recheck_context: dict | None = None,
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

    # Handle correction acknowledgment
    if analysis.message_type == "correct_previous_info":
        return _compose_from_diagnosis(
            public_safe_evidence, resolution_status,
            safety_reminder, wf_policy, policy,
            contradictions=contradictions,
            recheck_context=recheck_context,
        )

    if analysis.message_type == "ask_what_to_do":
        # If we have a specific diagnosis (e.g. bank-account verification needed),
        # answer from it rather than the generic guidance template.
        if public_safe_evidence.get("customer_safe_cause"):
            return _compose_from_diagnosis(
                public_safe_evidence, resolution_status,
                safety_reminder, wf_policy, policy,
                contradictions=contradictions,
                recheck_context=recheck_context,
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
            contradictions=contradictions,
            recheck_context=recheck_context,
        )

    # new_complaint or unknown: prefer a diagnosis-driven reply when we have one
    # (e.g. merchant settlement diagnosis), so the answer is specific & safe
    # rather than a fixed generic paragraph.
    if public_safe_evidence.get("customer_safe_cause"):
        return _compose_from_diagnosis(
            public_safe_evidence, resolution_status,
            safety_reminder, wf_policy, policy,
            contradictions=contradictions,
            recheck_context=recheck_context,
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


# ─── Sentence Limit Enforcement ────────────────────────────────

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Safety reminder keywords — if the last sentence is a safety reminder,
# we allow the response to exceed max_sentences by 1.
_SAFETY_KEYWORDS = ("pin", "otp", "mật khẩu", "password", "thẻ", "bảo mật")


def _enforce_sentence_limit(
    text: str,
    min_sentences: int = 2,
    max_sentences: int = 5,
) -> str:
    """Enforce a 2-5 sentence constraint on the composed response.

    - If under min_sentences: no padding (the response may be intentionally short,
      e.g. a contradiction notice or safety warning).
    - If over max_sentences: truncate to max_sentences, BUT allow max_sentences+1
      when the last sentence contains a safety reminder keyword.
    """
    if not text or not text.strip():
        return text

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]

    if len(sentences) <= max_sentences:
        return text

    # Check if the sentence at max_sentences index (0-based) is a safety reminder
    last_allowed = sentences[max_sentences] if len(sentences) > max_sentences else ""
    is_safety = any(kw in last_allowed.lower() for kw in _SAFETY_KEYWORDS)

    # Allow max+1 for safety reminder
    effective_max = max_sentences + 1 if is_safety else max_sentences
    trimmed = sentences[:effective_max]

    result = " ".join(trimmed)
    # Ensure it ends with punctuation
    if result and result[-1] not in ".!?":
        result += "."

    logger.debug(
        "[Composer] Sentence limit: %d → %d sentences (safety_extension=%s)",
        len(sentences), len(trimmed), is_safety,
    )
    return result


# ─── Public Entry Point ────────────────────────────────────────

def compose_customer_response(
    customer_message: str,
    message_analysis: MessageAnalysis,
    active_case_context: dict | None = None,
    public_safe_evidence: dict | None = None,
    resolution_status: str = "",
    contradictions: list | None = None,
    recheck_context: dict | None = None,
    customer_claims: dict | None = None,
    verified_evidence: dict | None = None,
) -> ComposedResponse:
    """Compose a customer-facing response.

    LLM-first with deterministic fallback from policy templates.

    Args:
        customer_message: Original customer message text.
        message_analysis: Output from analyze_customer_message.
        active_case_context: Active case state dict.
        public_safe_evidence: Output from to_public_safe_evidence.
        resolution_status: resolved | multiple_candidates | no_match | etc.
        contradictions: List of Contradiction objects (claim vs evidence mismatches).
        recheck_context: Dict with is_recheck, status_changed, old_status, new_status.
        customer_claims: Summary dict of customer-provided claims (for source labeling).
        verified_evidence: Summary dict of system-verified evidence (for source labeling).

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
        contradictions=contradictions,
        recheck_context=recheck_context,
        customer_claims=customer_claims,
        verified_evidence=verified_evidence,
    )

    if llm_result is not None and llm_result.public_message:
        llm_result.public_message = _enforce_sentence_limit(
            llm_result.public_message,
        )
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
        contradictions=contradictions,
        recheck_context=recheck_context,
    )

    det_result.public_message = _enforce_sentence_limit(
        det_result.public_message,
    )

    logger.info(
        "[Composer] Deterministic: tone=%s, needs_info=%s, msg_len=%d",
        det_result.tone, det_result.needs_more_info,
        len(det_result.public_message),
    )

    return det_result


# ═══════════════════════════════════════════════════════════════════
# Contextual Missing-Info Question Composer
#
# Replaces the old hardcoded _SAFE_QUESTION_MAP approach.
# Questions are now generated based on the complaint context:
#   - What the customer already provided
#   - What's still needed
#   - The active workflow
#   - The diagnosis so far
# ═══════════════════════════════════════════════════════════════════

# ─── Security Constants ─────────────────────────────────────────

_BLOCKED_QUESTION_FIELDS = frozenset({
    "password", "otp", "pin", "card_number", "private_key",
    "secret", "token", "api_key", "cvv", "cvc",
})

_WALLET_USER_KNOWN_FIELDS = frozenset({
    "user_id", "wallet_id", "phone", "email", "display_name",
})

_MERCHANT_KNOWN_FIELDS = frozenset({
    "merchant_id", "tax_code", "phone", "email", "display_name",
    "merchant_name",
})

# Safety keywords that must NEVER appear in generated questions
_BLOCKED_QUESTION_KEYWORDS = ("pin", "otp", "mật khẩu", "password", "số thẻ đầy đủ", "cvv")

# Field → human-readable Vietnamese label (for deterministic fallback)
_FIELD_LABELS: dict[str, str] = {
    "transaction_id": "mã giao dịch",
    "order_id": "mã đơn hàng",
    "bill_code": "mã hóa đơn",
    "customer_code": "mã khách hàng",
    "amount": "số tiền giao dịch",
    "bank_name": "ngân hàng đã dùng",
    "bank_reference": "mã tham chiếu ngân hàng",
    "transaction_time": "thời gian giao dịch gần đúng",
    "approximate_time_text": "thời gian giao dịch gần đúng",
    "approximate_date_text": "ngày giao dịch gần đúng",
    "service_type": "loại dịch vụ",
    "user_id": "tên tài khoản hoặc số điện thoại đăng ký",
    "phone": "số điện thoại đã đăng ký",
    "email": "email đã đăng ký",
    "merchant_id": "mã đối tác (Merchant ID)",
    "merchant_name": "tên đối tác/merchant",
    "tax_code": "mã số thuế",
    "payout_id": "mã thanh toán (Payout ID)",
    "batch_id": "mã lô thanh toán (Batch ID)",
    "provider_name": "nhà cung cấp dịch vụ",
    "settlement_date": "ngày settlement",
}


# ─── LLM Question Composer ──────────────────────────────────────

_QUESTION_SYSTEM_PROMPT = """\
You are a Vietnamese fintech customer support assistant. Generate follow-up \
questions to collect missing information from the customer.

Given:
- customer_message: what the customer wrote
- already_provided: information the customer has already given (DO NOT re-ask)
- still_needed: fields that are still missing
- workflow: the type of complaint (wallet_topup, train_ticket, utility_bill, \
  merchant_settlement_delay, fraud_account_lock)
- diagnosis_summary: what has been checked so far

Generate 1-3 concise, polite follow-up questions in Vietnamese that:
1. Acknowledge what the customer already provided — DO NOT re-ask for it
2. Ask for the most important missing information FIRST
3. Are specific to the customer's situation and workflow
4. Use natural, conversational Vietnamese
5. NEVER ask for: PIN, OTP, mật khẩu, số thẻ đầy đủ, private key, CVV

Return ONLY this JSON (no markdown):
{
  "questions": ["câu hỏi 1", "câu hỏi 2"]
}\
"""


def _filter_safe_fields(
    missing_fields: list[str],
    session: dict | None,
) -> list[str]:
    """Remove blocked and already-known fields. Security invariant."""
    known_fields: frozenset[str] = frozenset()
    if session is not None:
        subject_type = session.get("subject_type", "")
        if subject_type == "wallet_user":
            known_fields = _WALLET_USER_KNOWN_FIELDS
        elif subject_type == "merchant":
            known_fields = _MERCHANT_KNOWN_FIELDS

    safe: list[str] = []
    for field in missing_fields:
        lower = field.lower()
        if any(blocked in lower for blocked in _BLOCKED_QUESTION_FIELDS):
            continue
        if field in known_fields:
            continue
        safe.append(field)
    return safe


def _compose_questions_llm(
    missing_fields: list[str],
    extracted_info: dict,
    workflow: str,
    diagnosis: dict,
    customer_message: str,
) -> list[str] | None:
    """Use LLM to generate contextual questions. Returns None if unavailable."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    # Only include non-empty extracted values
    provided_summary = {k: v for k, v in extracted_info.items() if v}

    user_prompt = json.dumps({
        "customer_message": customer_message,
        "already_provided": provided_summary,
        "still_needed": missing_fields,
        "workflow": workflow,
        "diagnosis_summary": {
            "confirmed_facts": diagnosis.get("confirmed_public_facts", []),
            "issue_location": diagnosis.get("likely_issue_location", ""),
            "cause": diagnosis.get("customer_safe_cause", ""),
        },
    }, ensure_ascii=False)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, timeout=8.0)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _QUESTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=300,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        if not raw:
            return None

        parsed = json.loads(raw)
        questions = parsed.get("questions", [])

        if isinstance(questions, list) and questions:
            # Safety post-filter: reject any question mentioning blocked terms
            safe_questions = [
                q for q in questions
                if isinstance(q, str) and q.strip()
                and not any(kw in q.lower() for kw in _BLOCKED_QUESTION_KEYWORDS)
            ]
            return safe_questions[:3] if safe_questions else None

        return None

    except Exception as exc:
        logger.warning(
            "[QuestionComposer] LLM failed (%s): %s — using fallback",
            type(exc).__name__, exc,
        )
        return None


def _compose_questions_deterministic(
    missing_fields: list[str],
    extracted_info: dict,
    workflow: str,
    diagnosis: dict,
) -> list[str]:
    """Build context-aware questions without LLM.

    Improves over the old _SAFE_QUESTION_MAP by:
    1. Acknowledging what was already provided
    2. Listing remaining needs in a single contextual sentence
    3. Using workflow-aware framing
    """
    wf_policy = get_workflow_policy(workflow) if workflow and workflow != "unknown" else {}

    # Build readable labels for missing fields
    readable_missing: list[str] = []
    for field in missing_fields:
        label = _FIELD_LABELS.get(field)
        if label:
            readable_missing.append(label)
        elif field not in ("missing_fields",):
            # Fallback: use the field name itself, cleaned up
            readable_missing.append(field.replace("_", " "))

    if not readable_missing:
        return []

    # Build acknowledgment of what was provided
    provided_parts: list[str] = []
    for key, label in (
        ("amount", "số tiền"),
        ("bank_name", "ngân hàng"),
        ("transaction_id", "mã giao dịch"),
        ("order_id", "mã đơn hàng"),
    ):
        val = extracted_info.get(key)
        if val:
            provided_parts.append(f"{label} {val}")
    time_val = extracted_info.get("approximate_time_text") or extracted_info.get(
        "approximate_date_text",
    )
    if time_val:
        provided_parts.append(f"thời gian {time_val}")

    # Compose contextual question
    if provided_parts:
        ack = f"Bạn đã cung cấp {', '.join(provided_parts)}. "
    else:
        ack = ""

    needed_str = ", ".join(readable_missing)

    # Use workflow-specific safety reminder if available
    safety = wf_policy.get("safety_reminder", "")
    safety_suffix = f" {safety}" if safety else ""

    question = (
        f"{ack}Để xử lý nhanh hơn, bạn có thể cho biết thêm "
        f"{needed_str} không?{safety_suffix}"
    )

    return [question]


def compose_contextual_questions(
    missing_fields: list[str],
    extracted_info: dict,
    workflow: str,
    public_safe_diagnosis: dict,
    customer_message: str,
    session: dict | None = None,
) -> list[str]:
    """Generate context-aware missing info questions.

    LLM-first: generates specific questions based on what the customer
    said, what they've already provided, and what's still needed.

    Deterministic fallback: acknowledges provided info and asks for
    remaining fields in a contextual way.

    Security invariant: NEVER asks for PIN/OTP/password/card number.
    Already-known fields from session are excluded.

    Args:
        missing_fields: Field names still needed.
        extracted_info: What the customer has already provided.
        workflow: Active workflow name.
        public_safe_diagnosis: Output from evidence_mapper.
        customer_message: The customer's raw message text.
        session: Authenticated session (for known-field filtering).

    Returns:
        List of contextual, safe follow-up questions (max 3).
    """
    # 1. Security filter — always applied regardless of LLM/fallback
    safe_fields = _filter_safe_fields(missing_fields, session)
    if not safe_fields:
        return []

    # 2. Try LLM
    llm_result = _compose_questions_llm(
        safe_fields,
        extracted_info or {},
        workflow or "",
        public_safe_diagnosis or {},
        customer_message,
    )
    if llm_result:
        logger.info(
            "[QuestionComposer] LLM generated %d contextual questions",
            len(llm_result),
        )
        return llm_result

    # 3. Deterministic fallback (still context-aware)
    fallback = _compose_questions_deterministic(
        safe_fields,
        extracted_info or {},
        workflow or "",
        public_safe_diagnosis or {},
    )
    logger.info(
        "[QuestionComposer] Deterministic fallback: %d questions",
        len(fallback),
    )
    return fallback


# ─── Acknowledgement Response Composer ─────────────────────────

def compose_acknowledgement_response(
    resolution_status: str = "",
    workflow: str = "",
) -> str:
    """Compose a short, case-state-aware acknowledgement response.

    Called when the customer sends "được rồi", "ok", "cảm ơn" etc.
    while an active case exists.

    The response varies by resolution_status:
      - resolved: thank + can message again
      - no_match: thank + can send receipt/reference later
      - unresolved/other: thank + issue is recorded

    Returns a natural Vietnamese text — NEVER hardcoded to one exact phrase.
    """
    # Get workflow display noun from registry
    noun = "giao dịch"
    try:
        from fintech_agent.workflows.workflow_registry import get_registry
        registry = get_registry()
        noun = registry.get_display_noun(workflow) or "giao dịch"
    except Exception:
        pass

    # Load safety reminder from policy
    policy = load_response_policy()
    safety_hint = policy.get("global_safety_reminder", "")
    safety_short = "Vui lòng không gửi PIN, OTP hoặc mật khẩu."
    if safety_hint:
        # Use a short form if the full one is too long
        safety_short = safety_hint if len(safety_hint) < 80 else safety_short

    status = (resolution_status or "").lower()

    if status in ("resolved", "completed", "success"):
        # Case resolved in chat
        return (
            f"Cảm ơn bạn đã xác nhận. Nội dung trao đổi về {noun} đã được ghi nhận. "
            f"Nếu bạn cần hỗ trợ thêm, bạn có thể gửi tiếp trong khung chat này. "
            f"{safety_short}"
        )

    if status in ("no_match",):
        # No matching data found — remind they can send evidence later
        return (
            f"Cảm ơn bạn. Mình đã ghi nhận nội dung trao đổi. "
            f"Nếu bạn có thêm mã tham chiếu ngân hàng hoặc biên lai, "
            f"bạn có thể gửi tiếp trong khung chat này để mình kiểm tra lại. "
            f"{safety_short}"
        )

    # Unresolved / processing / other
    return (
        f"Cảm ơn bạn. Mình đã ghi nhận nội dung trao đổi. "
        f"Nếu bạn cần kiểm tra thêm hoặc có thêm thông tin, "
        f"bạn có thể gửi tiếp trong khung chat này. "
        f"{safety_short}"
    )


# ─── Verification-aware response composer ──────────────────────

def compose_from_verification(
    latest_message: str,
    router_result: MessageAnalysis,
    verification: Any,
    response_policy: dict | None = None,
) -> ComposedResponse:
    """Compose a customer response using ONLY the verification result.

    This is the preferred entry point for the generic verification framework.
    It uses ONLY:
      - latest_message (what the customer said)
      - router_result (LLM analysis of intent/workflow)
      - verification (VerificationResult from verify_account_issue)
      - response_policy (from YAML config)

    No stale case context. No external diagnosis. No old workflow data.

    Args:
        latest_message: The customer's latest message text.
        router_result: MessageAnalysis from LLM message understanding.
        verification: VerificationResult from verify_account_issue().
        response_policy: Loaded customer_response_policy dict.

    Returns:
        ComposedResponse ready for the customer.
    """
    # Extract from verification (supports both dataclass and dict)
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    issue_exists = _get(verification, "issue_exists", False)
    issue_status = _get(verification, "issue_status", "no_match")
    verified_evidence = _get(verification, "verified_evidence", {}) or {}
    customer_claims_data = _get(verification, "customer_claims", {}) or {}
    contradictions = _get(verification, "contradictions", []) or []
    root_cause = _get(verification, "root_cause", {}) or {}
    missing_evidence = _get(verification, "missing_evidence", []) or []
    workflow_id = _get(verification, "workflow_id", "")
    identity_resolved = _get(verification, "identity_resolved", False)

    # Build public-safe evidence dict for the LLM composer
    public_safe_evidence = {
        "what_was_checked": _get(verification, "data_checked", []),
        "confirmed_public_facts": [],
        "customer_safe_cause": root_cause.get("reason", ""),
        "likely_issue_location": root_cause.get("issue_location", ""),
        "next_step": "",
        "confidence": root_cause.get("confidence", "low"),
        "workflow": workflow_id,
    }

    # Build confirmed facts from verified evidence
    if verified_evidence.get("status"):
        public_safe_evidence["confirmed_public_facts"].append(
            f"trạng thái giao dịch: {verified_evidence['status']}"
        )
    if verified_evidence.get("bank_status"):
        public_safe_evidence["confirmed_public_facts"].append(
            f"trạng thái ngân hàng: {verified_evidence['bank_status']}"
        )

    # Map existing evidence keys into the diagnosis
    for key in ("customer_safe_cause", "likely_issue_location", "next_step",
                "customer_action_needed", "case_status"):
        val = verified_evidence.get(key)
        if val:
            public_safe_evidence[key] = val

    # Map resolution status for the composer
    if issue_status == "verified_issue_found":
        resolution_status = "resolved"
    elif issue_status == "contradiction":
        resolution_status = "amount_mismatch"
    elif issue_status == "insufficient_evidence":
        resolution_status = "need_more_info"
    elif issue_status == "no_issue_found":
        resolution_status = "no_match"
    else:
        resolution_status = "no_match"

    if not identity_resolved:
        resolution_status = "invalid_session"

    # Try LLM composition first
    composed = _compose_with_llm(
        customer_message=latest_message,
        analysis=router_result,
        active_case_context={"selected_workflow": workflow_id},
        public_safe_evidence=public_safe_evidence,
        resolution_status=resolution_status,
        contradictions=contradictions,
        customer_claims=customer_claims_data,
        verified_evidence=verified_evidence,
    )

    if composed:
        # Enforce sentence limit
        composed.public_message = _enforce_sentence_limit(
            composed.public_message, max_sentences=5,
        )
        return composed

    # Deterministic fallback
    fallback = _compose_deterministic(
        customer_message=latest_message,
        analysis=router_result,
        active_case_context={"selected_workflow": workflow_id},
        public_safe_evidence=public_safe_evidence,
        resolution_status=resolution_status,
        contradictions=contradictions,
    )
    fallback.public_message = _enforce_sentence_limit(
        fallback.public_message, max_sentences=5,
    )
    if missing_evidence:
        fallback.needs_more_info = True
    return fallback


