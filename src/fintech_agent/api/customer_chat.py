"""Customer-facing chat endpoint — generic, data-driven pipeline.

POST /api/customer-chat

Pipeline:
  Customer message
  → Load session context
  → Load active case context
  → LLM Message Understanding (message_analyzer)
  → Sensitive info guard
  → Ownership validation
  → Generic resolver / evidence lookup
  → Workflow router + rule engine (existing, for new complaints)
  → Public-safe evidence mapper
  → LLM Response Composer
  → Output guardrail
  → Customer-facing response

SECURITY INVARIANTS (non-negotiable):
  - Response MUST NOT include: evidence_bundle, rule_decision, diagnosis,
    recommended_action, risk_level, draft_output, action_draft,
    approval_status, approval_required, resolution_ticket, audit_event_ids,
    conflicts, errors, generated_response internals, MCP tool results.
  - Response MUST NOT include: risk_score, fraud_status, fraud signals,
    settlement_batch internals, merchant_payout internals.
  - Missing info questions MUST NOT ask for: password, OTP, PIN, full card
    number, private key, or any sensitive credential.
  - When session is authenticated, MUST NOT ask for fields already known
    (phone, email, user_id for wallet_user; merchant_id, tax_code for merchant).
  - Identity (user_id, merchant_id) comes from SERVER-SIDE session only,
    NEVER from frontend request body.
  - Transaction ownership MUST be validated: transaction.user_id == session.user_id.
  - Merchant identity MUST be validated: message merchant_id must match session.
  - On mismatch: do NOT reveal data, return safe refusal response.
  - LLM MUST NOT decide refund/force-success/unlock/payout.
"""

from __future__ import annotations

import logging
import os
import re
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from fintech_agent.api.service import get_case_service
from fintech_agent.database.repository_factory import (
    get_mock_session_repo,
    get_transaction_repo,
)
from fintech_agent.repositories.base import RecordNotFound
from fintech_agent.schemas.enums import CaseStatus
from fintech_agent.api.chat_trace import CustomerChatTrace

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["customer-chat"])


# ─── Request / Response Models ──────────────────────────────────

class CustomerChatRequest(BaseModel):
    """Customer chat input — message text + optional session."""
    message: str = Field(
        ..., min_length=1, max_length=5000,
        description="Nội dung khiếu nại của khách hàng",
    )
    session_id: str | None = Field(
        default=None, max_length=200,
        description="Mock session ID (from demo login)",
    )
    debug: bool = Field(
        default=False,
        description="Return debug trace (dev mode only)",
    )


class CustomerChatResponse(BaseModel):
    """Sanitized response for customer — NO internal fields."""
    public_case_id: str = Field(description="Mã tra cứu công khai")
    status: str = Field(
        description="received | need_more_info | processing | need_login"
    )
    public_response: str = Field(description="Câu trả lời công khai cho khách")
    missing_info_questions: list[str] = Field(
        default_factory=list,
        description="Câu hỏi bổ sung thông tin (nếu cần)",
    )
    debug_trace: dict | None = Field(
        default=None,
        description="Debug trace (dev mode only, never in production)",
    )


# ─── Security Constants (kept for backward compat) ──────────────
# Question generation now uses compose_contextual_questions() from
# response_composer.py — context-aware, LLM-first with safe fallback.

# Fields we NEVER ask the customer about
_BLOCKED_QUESTION_FIELDS = frozenset({
    "password", "otp", "pin", "card_number", "private_key",
    "secret", "token", "api_key", "cvv", "cvc",
})

# Fields to skip when wallet_user session is authenticated
_WALLET_USER_KNOWN_FIELDS = frozenset({
    "user_id", "wallet_id", "phone", "email", "display_name",
})

# Fields to skip when merchant session is authenticated
_MERCHANT_KNOWN_FIELDS = frozenset({
    "merchant_id", "tax_code", "phone", "email", "display_name",
    "merchant_name",
})


# ─── Imports from new pipeline modules ──────────────────────────

from fintech_agent.llm.message_analyzer import (
    NON_CASE_MESSAGE_TYPES,
    ActiveCaseContext,
    ExtractedFields,
    MessageAnalysis,
    analyze_customer_message,
    load_response_policy,
)
from fintech_agent.api.generic_resolver import (
    NO_MATCH_INSIST_RESPONSE,
    ResolutionResult,
    amount_mismatch_message,
    no_lock_evidence_message,
    no_match_message,
    resolve_case_evidence,
)
from fintech_agent.api.customer_claims import (
    CustomerClaims,
    VerifiedEvidence,
    Contradiction,
    detect_contradictions,
    get_unverified_claims,
)
from fintech_agent.safety.evidence_mapper import to_public_safe_evidence
from fintech_agent.llm.response_composer import (
    compose_customer_response,
    compose_contextual_questions,
)
from fintech_agent.safety.output_guardrail import (
    check_evidence_grounding,
    check_response_safety,
    sanitize_customer_text,
)
from fintech_agent.safety.redaction import redact_sensitive
from fintech_agent.api.chat_handoff import finalize_customer_chat_and_handoff, get_ticket_store
from datetime import datetime, timezone


# Explicit "I want a human" detection — generic keywords, NOT the exact
# complaint. Triggers a back-office handoff.
_STAFF_REQUEST_RE = re.compile(
    r"(?:gặp|cần|muốn|chuyển|nói\s+chuyện\s+với|liên\s+hệ)\s+"
    r"(?:nhân\s+viên|tổng\s+đài|hỗ\s+trợ\s+viên|con\s+người|nhân\s+sự)"
    r"|nhân\s+viên\s+(?:hỗ\s+trợ|tư\s+vấn)"
    r"|talk\s+to\s+(?:a\s+)?(?:human|agent|staff)"
    r"|speak\s+to\s+(?:a\s+)?(?:human|agent|staff)",
    re.IGNORECASE,
)


def _record_turn(ctx, role: str, text: str) -> None:
    """Append a REDACTED message to the conversation transcript."""
    if not text:
        return
    ctx.transcript.append({
        "role": role,
        "text": redact_sensitive(text),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ─── Active Case Context (per session) ──────────────────────────

_session_context: dict[str, ActiveCaseContext] = {}
# Wall-clock time of each session's last message, for the idle TTL below.
_session_last_active: dict[str, float] = {}

# Reset a chat after this many seconds of inactivity (matches the frontend's
# 3-minute localStorage TTL so both sides forget a stale conversation together).
SESSION_IDLE_TTL_SECONDS = 3 * 60


def _get_active_context(session_id: str | None) -> ActiveCaseContext:
    """Load a session's active context, expiring it after the idle TTL.

    If the customer has been silent longer than SESSION_IDLE_TTL_SECONDS, the
    old context is dropped and a fresh one is returned — so they get a clean
    chat, not a stale case/diagnosis from minutes ago.
    """
    if not session_id:
        return ActiveCaseContext()
    last = _session_last_active.get(session_id)
    if last is not None and (time.time() - last) > SESSION_IDLE_TTL_SECONDS:
        _session_context.pop(session_id, None)
        _session_last_active.pop(session_id, None)
        logger.info(
            "[CustomerChat] Session %s idle > %ds — context expired, fresh chat",
            session_id, SESSION_IDLE_TTL_SECONDS,
        )
    return _session_context.get(session_id, ActiveCaseContext())


def reset_session_contexts() -> None:
    """Clear all in-memory chat contexts (for tests)."""
    _session_context.clear()
    _session_last_active.clear()


# Workflows the resolver/rule-engine can actually diagnose.
# Now read from the workflow registry — no more hardcoded frozenset.
def _known_workflows() -> frozenset[str]:
    """Get all known workflow IDs from the registry.

    Drop-in replacement for the old ``_KNOWN_WORKFLOWS`` frozenset.
    Falls back to a static set if registry import fails.
    """
    try:
        from fintech_agent.workflows.workflow_registry import get_registry
        return get_registry().known_workflow_ids()
    except Exception:
        return frozenset({
            "wallet_topup", "fraud_account_lock", "train_ticket",
            "utility_bill", "merchant_settlement_delay",
        })


# Legacy alias for any code that still references _KNOWN_WORKFLOWS
_KNOWN_WORKFLOWS = _known_workflows()


def _is_workflow_switch(ctx: ActiveCaseContext, analysis: MessageAnalysis) -> bool:
    """True when the customer raises a different known workflow than the active case.

    Data-driven from the LLM's workflow_hint + message_type — never from phrase
    matching. A pure follow-up/info/status message about the SAME service is not
    a switch even if the hint momentarily differs.
    """
    known = _known_workflows()
    return (
        bool(ctx.selected_workflow)
        and analysis.workflow_hint in known
        and analysis.workflow_hint != ctx.selected_workflow
        and analysis.message_type in ("workflow_switch", "new_complaint")
    )


def _start_fresh_case_for_switch(
    old_ctx: ActiveCaseContext,
    message: str,
) -> ActiveCaseContext:
    """Start a fresh logical case on a workflow switch.

    Keeps the conversation (transcript) and identity, but drops ALL case-specific
    state (workflow, diagnosis, evidence, claims, contradictions, extracted info)
    so the new workflow is answered only from its own data.
    """
    fresh = ActiveCaseContext(
        subject_type=old_ctx.subject_type,
        transcript=list(old_ctx.transcript),       # same chat, continued
        customer_problem=redact_sensitive(message),  # the new problem
        customer_emotion=old_ctx.customer_emotion,
    )
    # Fresh claim/evidence trackers — never reuse the old workflow's facts.
    fresh._customer_claims = CustomerClaims()
    fresh._verified_evidence = VerifiedEvidence()
    fresh._contradictions = []
    return fresh


def _apply_evidence_grounding(
    text: str,
    resolution: ResolutionResult,
    ctx: ActiveCaseContext,
    workflow: str,
) -> str:
    """Enforce the fact-source rule on a final customer reply.

    Confirmed-status wording must be backed by a verified entity on the
    logged-in account (this turn's resolver result, or the case's bound
    evidence from an earlier verified turn), and any amount stated alongside
    it must equal the VERIFIED amount — never the customer's claim. If the
    check fails, the reply is replaced with an honest not-found fallback.
    """
    bound = getattr(ctx, "_verified_evidence", None)
    entity = resolution.resolved_entity_id or (
        getattr(bound, "resolved_entity_id", "") or None
    )
    amount = (
        resolution.verified_amount
        if resolution.verified_amount is not None
        else getattr(bound, "verified_amount", None)
    )
    status = resolution.resolution_status
    if status not in ("resolved", "amount_mismatch") and entity:
        # Bound evidence from an earlier verified turn still backs the case.
        status = "resolved"

    # Account-lock claims are only allowed when the account record itself
    # shows lock evidence — the customer's claim never counts.
    lock_evidence: bool | None = None
    is_account_flow = (
        (workflow or "") == "fraud_account_lock"
        or resolution.resolved_entity_type == "account"
    )
    if is_account_flow:
        ev = resolution.public_safe_evidence or {}
        bound_status = str(getattr(bound, "verified_status", "") or "")
        lock_evidence = bool(
            ev.get("lock_evidence_found")
            or resolution.verified_status in ("locked", "restricted", "under_review")
            or bound_status in ("locked", "restricted", "under_review")
        )

    fallback = (
        no_lock_evidence_message() if is_account_flow and not lock_evidence
        else no_match_message(workflow or "")
    )
    result = check_evidence_grounding(
        text,
        resolver_status=status,
        verified_entity_id=entity,
        verified_amount=amount,
        fallback_text=fallback,
        lock_evidence=lock_evidence,
    )
    if result.is_safe:
        return text
    logger.warning(
        "[CustomerChat] Evidence grounding replaced reply (status=%s, entity=%s)",
        resolution.resolution_status, entity,
    )
    return result.sanitized_text or no_match_message(workflow or "")


# ─── Helper Functions ───────────────────────────────────────────


def _build_off_topic_reply(
    message_type: str,
    session: dict | None,
    policy: dict,
) -> str:
    """Build a safe reply for off-topic / greeting messages.

    Does NOT create a case or ask for transaction info.
    Just redirects the customer back to the supported scope.
    """
    # Check policy for custom templates first
    greeting_reply = policy.get("greeting_reply", "")
    out_of_scope_reply = policy.get("out_of_scope_reply", "")

    if message_type == "greeting" and greeting_reply:
        return greeting_reply
    if message_type == "out_of_scope" and out_of_scope_reply:
        return out_of_scope_reply

    # Determine display name from session for personalization
    name = ""
    if session:
        name = session.get("display_name", "") or ""

    if message_type == "greeting":
        hello = f"Xin chào{' ' + name if name else ''}!"
        return (
            f"{hello} Tôi là trợ lý hỗ trợ khiếu nại. "
            "Bạn có thể mô tả vấn đề bạn đang gặp — ví dụ: "
            "nạp tiền không vào ví, chưa nhận vé tàu, hoặc hóa đơn chưa được ghi nhận."
        )

    # out_of_scope
    return (
        "Tôi là trợ lý hỗ trợ khiếu nại giao dịch. "
        "Nếu bạn đang gặp vấn đề về nạp tiền, mua vé, thanh toán hóa đơn "
        "hoặc giải ngân merchant, hãy mô tả chi tiết để tôi hỗ trợ nhé."
    )


def _map_to_customer_status(internal_status: CaseStatus | str) -> str:
    """Map internal CaseStatus to safe customer-facing status."""
    status_val = (
        internal_status.value
        if isinstance(internal_status, CaseStatus)
        else str(internal_status)
    )
    if status_val in ("new", "closed", "rejected"):
        return "received"
    elif status_val in ("waiting_info", "missing_info"):
        return "need_more_info"
    else:
        return "processing"


def _extract_public_response(state: dict) -> str:
    """Extract the customer-safe response text from the agent state."""
    gr = state.get("generated_response")
    if gr is not None:
        draft = None
        if hasattr(gr, "customer_reply_draft"):
            draft = gr.customer_reply_draft
        elif isinstance(gr, dict):
            draft = gr.get("customer_reply_draft")
        if draft and isinstance(draft, str) and draft.strip():
            return draft.strip()

    rt = state.get("resolution_ticket")
    if rt is not None:
        draft = None
        if hasattr(rt, "customer_reply_draft"):
            draft = rt.customer_reply_draft
        elif isinstance(rt, dict):
            draft = rt.get("customer_reply_draft")
        if draft and isinstance(draft, str) and draft.strip():
            return draft.strip()

    policy = load_response_policy()
    return policy.get("generic_fallback_response", "") or (
        "Chúng tôi đã ghi nhận khiếu nại của bạn. "
        "Bộ phận hỗ trợ sẽ kiểm tra và phản hồi trong thời gian sớm nhất."
    )


def _build_complaint_with_identity(
    raw_message: str,
    session: dict,
) -> str:
    """Inject trusted identity context from session into the complaint."""
    subject_type = session.get("subject_type", "")
    parts = [raw_message]

    if subject_type == "wallet_user":
        for key, label in [
            ("user_id", "User ID"), ("wallet_id", "Wallet ID"),
            ("phone", "SĐT"), ("email", "Email"),
        ]:
            val = session.get(key)
            if val:
                parts.append(f"[{label}: {val}]")

    elif subject_type == "merchant":
        for key, label in [
            ("merchant_id", "Merchant ID"), ("tax_code", "MST"),
            ("phone", "SĐT"), ("email", "Email"),
        ]:
            val = session.get(key)
            if val:
                parts.append(f"[{label}: {val}]")

    return " ".join(parts)


# ─── Ownership Validation ──────────────────────────────────────

# Generic ID extraction patterns
_TXN_ID_PATTERN = re.compile(
    r"""
    (?:TXN[-_][\w]+)                     # TXN_xxx, TXN-xxx patterns
    |(?:mã\s+(?:giao\s+dịch|GD)\s*:?\s*) # "mã giao dịch:" prefix
     ([\w-]+)                             # followed by actual ID
    """,
    re.IGNORECASE | re.VERBOSE,
)

_MERCHANT_ID_PATTERN = re.compile(
    r"(?:MC[\w]+)|(?:merchant[\s_-]*(?:id)?[\s:]*)([\w]+)",
    re.IGNORECASE,
)

_TAX_CODE_PATTERN = re.compile(
    r"(?:MST|mã\s+số\s+thuế)\s*:?\s*(\d{10,14})",
    re.IGNORECASE,
)


def _extract_transaction_ids(message: str) -> list[str]:
    """Extract transaction IDs from customer message."""
    txn_ids: list[str] = []
    for match in _TXN_ID_PATTERN.finditer(message):
        txn_id = match.group(1) or match.group(0)
        txn_id = txn_id.strip()
        if txn_id:
            txn_ids.append(txn_id)
    return txn_ids


def _extract_merchant_ids(message: str) -> list[str]:
    """Extract merchant IDs from customer message."""
    ids: list[str] = []
    for match in _MERCHANT_ID_PATTERN.finditer(message):
        mid = match.group(1) or match.group(0)
        mid = mid.strip()
        if mid:
            ids.append(mid)
    return ids


def _extract_tax_codes(message: str) -> list[str]:
    """Extract tax codes from customer message."""
    return [m.group(1).strip() for m in _TAX_CODE_PATTERN.finditer(message) if m.group(1)]


_WALLET_OWNERSHIP_MISMATCH_RESPONSE = (
    "Chúng tôi chưa xác minh được giao dịch này thuộc tài khoản của bạn. "
    "Vui lòng kiểm tra lại mã giao dịch hoặc liên hệ hỗ trợ."
)

_MERCHANT_IDENTITY_MISMATCH_RESPONSE = (
    "Thông tin merchant/MST trong tin nhắn không khớp với tài khoản đã đăng nhập. "
    "Vui lòng kiểm tra lại hoặc liên hệ bộ phận hỗ trợ đối tác."
)


def _validate_wallet_user_ownership(
    message: str,
    session: dict,
) -> str | None:
    """Validate transaction ownership. Returns error message or None."""
    session_user_id = session.get("user_id")
    if not session_user_id:
        return None

    txn_ids = _extract_transaction_ids(message)
    if not txn_ids:
        return None

    try:
        txn_repo = get_transaction_repo()
    except Exception as exc:
        logger.error("[Ownership] Failed to get transaction repo: %s", exc)
        return None

    for txn_id in txn_ids:
        try:
            txn = txn_repo.get_by_id(txn_id)
        except RecordNotFound:
            continue
        except Exception as exc:
            logger.error("[Ownership] Failed to fetch transaction %s: %s", txn_id, exc)
            continue

        if txn.user_id != session_user_id:
            logger.warning(
                "[Ownership] MISMATCH: session user=%s tried to access txn=%s (owner=%s)",
                session_user_id, txn_id, txn.user_id,
            )
            return _WALLET_OWNERSHIP_MISMATCH_RESPONSE

    return None


def _validate_merchant_identity(
    message: str,
    session: dict,
) -> str | None:
    """Validate merchant identity. Returns error message or None."""
    session_merchant_id = session.get("merchant_id")
    session_tax_code = session.get("tax_code")

    mentioned_mids = _extract_merchant_ids(message)
    if mentioned_mids and session_merchant_id:
        for mid in mentioned_mids:
            if mid.upper() != session_merchant_id.upper():
                logger.warning(
                    "[Ownership] Merchant ID mismatch: session=%s, message=%s",
                    session_merchant_id, mid,
                )
                return _MERCHANT_IDENTITY_MISMATCH_RESPONSE

    mentioned_tax_codes = _extract_tax_codes(message)
    if mentioned_tax_codes and session_tax_code:
        for tc in mentioned_tax_codes:
            if tc != session_tax_code:
                logger.warning(
                    "[Ownership] Tax code mismatch: session=%s, message=%s",
                    session_tax_code, tc,
                )
                return _MERCHANT_IDENTITY_MISMATCH_RESPONSE

    return None


# ─── Extracted Info Persistence Helpers ─────────────────────────

# Mapping from ExtractedFields attribute → extracted_info key
_FIELD_TO_KEY = {
    "transaction_id": "transaction_id",
    "order_id": "order_id",
    "bill_code": "bill_code",
    "customer_code": "customer_code",
    "amount": "amount",
    "bank_name": "bank_name",
    "bank_reference": "bank_reference",
    "approximate_time_text": "approximate_time_text",
    "approximate_date_text": "approximate_date_text",
    "provider_name": "provider_name",
}

# Which extracted_info keys satisfy which missing_fields entries
_FIELD_SATISFIES = {
    "amount": ["amount"],
    "bank_name": ["bank_name"],
    "bank_reference": ["bank_reference", "bank_name"],
    "approximate_time_text": ["transaction_time"],
    "approximate_date_text": ["transaction_time"],
    "transaction_id": ["transaction_id"],
    "order_id": ["order_id", "transaction_id"],
    "bill_code": ["bill_code"],
}


def _merge_extracted_into_context(
    ctx: ActiveCaseContext,
    extracted: "ExtractedFields",
) -> None:
    """Persist newly extracted fields into ctx.extracted_info.

    Only overwrites if the new value is non-empty (accumulate, don't lose).
    """

    for attr, key in _FIELD_TO_KEY.items():
        val = getattr(extracted, attr, None)
        if val:  # non-None, non-empty, non-zero
            ctx.extracted_info[key] = val


def _apply_context_to_extraction(
    ctx: ActiveCaseContext,
    extracted: "ExtractedFields",
) -> None:
    """Merge accumulated ctx.extracted_info back into extraction.

    So the resolver sees ALL info from ALL messages, not just the current one.
    """
    for attr, key in _FIELD_TO_KEY.items():
        current = getattr(extracted, attr, None)
        if not current:
            stored = ctx.extracted_info.get(key)
            if stored:
                setattr(extracted, attr, stored)


def _recalculate_missing_fields(ctx: ActiveCaseContext) -> None:
    """Remove missing_fields that are now satisfied by extracted_info."""
    if not ctx.missing_fields:
        return

    satisfied: set[str] = set()
    for key, val in ctx.extracted_info.items():
        if val:
            satisfies = _FIELD_SATISFIES.get(key, [key])
            for f in satisfies:
                satisfied.add(f)

    ctx.missing_fields = [f for f in ctx.missing_fields if f not in satisfied]


def _subtract_provided_fields(
    missing_info: list[str],
    extracted_info: dict,
) -> list[str]:
    """Remove fields from resolver's missing_info that customer already provided."""
    if not missing_info or not extracted_info:
        return missing_info

    satisfied: set[str] = set()
    for key, val in extracted_info.items():
        if val:
            satisfies = _FIELD_SATISFIES.get(key, [key])
            for f in satisfies:
                satisfied.add(f)

    return [f for f in missing_info if f not in satisfied]


# ─── Ticket Decision Logic ─────────────────────────────────────

def _should_create_ticket(
    analysis: MessageAnalysis,
    resolution: ResolutionResult,
    state: dict | None,
    ctx: ActiveCaseContext,
) -> bool:
    """Decide if a back-office ticket is needed.

    Returns False for interactions that don't warrant staff attention:
    - Off-topic / greeting messages
    - FAQ questions answered fully in-chat
    - Cases fully resolved without needing staff action

    Returns True when staff attention is warranted:
    - Rule engine produced a decision requiring review
    - Approval is required
    - Unresolved financial issue with active case
    - Contradictions detected between claims and evidence
    """
    # NO ticket for off-topic, greeting, or acknowledgements
    if analysis.message_type in NON_CASE_MESSAGE_TYPES or analysis.message_type == "thank_you":
        return False

    # NO ticket when the system could not find ANY matching data for the
    # logged-in account (no verified evidence). staff_handling_required is
    # false by default here — the customer must supply stronger evidence, ask
    # for staff explicitly, or a rule must require review first.
    if resolution.resolution_status == "no_match":
        return False

    # YES ticket if workflow state has rule decisions or approval needed
    if state and isinstance(state, dict):
        if state.get("rule_decision"):
            return True
        if state.get("approval_required"):
            return True

    # YES ticket if contradictions detected (staff needs to review)
    if hasattr(ctx, '_contradictions') and ctx._contradictions:
        return True

    # YES ticket if unresolved financial issue with active case (but only when
    # there IS verified evidence to work with — no_match is excluded above).
    if ctx.case_id and resolution.resolution_status != "resolved":
        return True

    # Otherwise (e.g. resolved in chat, FAQ answered) → no staff handoff needed.
    # A ticket is still created when the chat actually ends/expires via the
    # /customer-chat/handoff endpoint if staff handling is required.
    return False


# ─── Endpoint ──────────────────────────────────────────────────

@router.post(
    "/customer-chat",
    response_model=CustomerChatResponse,
    response_model_exclude_none=True,
    status_code=201,
    summary="Customer complaint submission (safe, sanitized)",
    description=(
        "Customer-facing endpoint. Accepts complaint text + optional session_id, "
        "creates an internal case, and returns ONLY sanitized public response. "
        "No internal data is ever exposed."
    ),
)
async def customer_chat(req: CustomerChatRequest) -> CustomerChatResponse:
    """Handle customer complaint submission — generic pipeline.

    Flow:
      1. Load session context
      2. Load active case context
      3. LLM Message Understanding
      4. Sensitive info guard
      5. Ownership validation
      6. Generic resolver / evidence lookup
      7. If new complaint → run workflow
      8. Public-safe evidence mapper
      9. LLM Response Composer
      10. Output guardrail
      11. Return sanitized response
    """
    logger.info("[CustomerChat] Received complaint (length=%d)", len(req.message))

    policy = load_response_policy()

    # ── Step 1: Load session context ──
    session: dict | None = None
    if req.session_id:
        try:
            repo = get_mock_session_repo()
            session = repo.get_session(req.session_id)
        except Exception as exc:
            logger.error(
                "[CustomerChat] Failed to load session %s: %s",
                req.session_id, exc,
            )
            session = None

        if session is None and req.session_id:
            return CustomerChatResponse(
                public_case_id="",
                status="need_login",
                public_response=(
                    "Vui lòng đăng nhập để hệ thống xác minh tài khoản của bạn."
                ),
                missing_info_questions=[],
            )

    # ── Step 2: Load active case context (expires after idle TTL) ──
    ctx = _get_active_context(req.session_id)
    # Mark this request as activity so the idle timer counts from now.
    if req.session_id:
        _session_last_active[req.session_id] = time.time()

    # Record the customer turn (redacted) + first-problem / identity for handoff
    _record_turn(ctx, "customer", req.message)
    if not ctx.customer_problem:
        ctx.customer_problem = redact_sensitive(req.message)
    if session:
        ctx.subject_type = session.get("subject_type", "") or ctx.subject_type

    # ── Create debug trace ──
    trace = CustomerChatTrace(
        session_id=req.session_id or "",
        message=req.message[:200],  # truncate for safety
        active_case_id_before=ctx.case_id,
    )
    trace.case_context.selected_workflow = ctx.selected_workflow
    trace.case_context.missing_fields_before = list(ctx.missing_fields)
    trace.case_context.extracted_info_before = dict(ctx.extracted_info)
    _is_dev = os.environ.get("ENVIRONMENT", "local") in ("local", "development", "dev")

    # ── Step 3: LLM Message Understanding ──
    case_context = {
        "selected_workflow": ctx.selected_workflow,
        "service_type": ctx.service_type,
        "awaiting_field": ctx.awaiting_field,
        "has_active_case": bool(ctx.case_id),
    }
    session_ctx = {
        "subject_type": session.get("subject_type", "") if session else "",
        "is_authenticated": session is not None,
    }

    analysis = analyze_customer_message(req.message, case_context, session_ctx)

    # Amount claimed in THIS message (before accumulated context is merged back
    # into the extraction) — used to decide whether the customer is actively
    # (re)asserting an amount vs. merely following up.
    _turn_claimed_amount = analysis.extracted.amount

    # ── Step 3a2: Workflow switch — customer raised a DIFFERENT service ──
    # Start a fresh logical case for the new workflow so no diagnosis, evidence,
    # claims, or wording from the previous workflow leaks into the new answer.
    if _is_workflow_switch(ctx, analysis):
        logger.info(
            "[CustomerChat] Workflow switch %s → %s — fresh case, no cross-workflow reuse",
            ctx.selected_workflow, analysis.workflow_hint,
        )
        ctx = _start_fresh_case_for_switch(ctx, req.message)
        case_context = {
            "selected_workflow": "", "service_type": "",
            "awaiting_field": "", "has_active_case": False,
        }

    # ── Step 3b: Persist extracted fields into active case context ──
    # Accumulate across messages — don't discard previous extractions.
    _merge_extracted_into_context(ctx, analysis.extracted)

    # ── Step 3b2: Track customer claims (separate from verified evidence) ──
    if not hasattr(ctx, '_customer_claims'):
        ctx._customer_claims = CustomerClaims()
    if not hasattr(ctx, '_verified_evidence'):
        ctx._verified_evidence = VerifiedEvidence()
    if not hasattr(ctx, '_contradictions'):
        ctx._contradictions = []

    # Merge into claim tracker (claims are UNVERIFIED)
    ctx._customer_claims.merge_extracted_fields(
        analysis.extracted,
        is_correction=analysis.is_correction,
    )

    # On correction: clear stale diagnosis so we re-evaluate
    if analysis.is_correction and ctx.last_diagnosis:
        logger.info(
            "[CustomerChat] Customer corrected previous info — clearing stale diagnosis"
        )
        ctx.last_diagnosis = {}
        ctx._verified_evidence = VerifiedEvidence()
        ctx._contradictions = []

    # ── Step 3c: Merge accumulated context back into analysis.extracted ──
    # So resolver sees ALL info gathered across messages, not just this one.
    _apply_context_to_extraction(ctx, analysis.extracted)

    # ── Step 3d: Recalculate missing_fields after extraction ──
    missing_before = list(ctx.missing_fields)
    _recalculate_missing_fields(ctx)
    missing_after = list(ctx.missing_fields)

    # ── Step 3e: Active case workflow is AUTHORITATIVE — unless a clear
    #    workflow mismatch is detected ──
    # For anything that is not a brand-new complaint OR a workflow switch,
    # the active case's selected_workflow drives evidence lookup, diagnosis,
    # and guardrail — NOT the per-message workflow_hint (which the LLM may
    # guess wrong, e.g. classifying a train_ticket follow-up as wallet_topup).
    #
    # HOWEVER: if the analyzer detected a STRONG signal for a DIFFERENT
    # workflow (e.g. "tài khoản bị khóa" while wallet_topup is active)
    # and set belongs_to_active_case=False + message_type="workflow_switch",
    # we must NOT override — this IS a genuine workflow switch.
    is_mismatch_switch = (
        ctx.selected_workflow
        and analysis.workflow_hint != ctx.selected_workflow
        and analysis.workflow_hint != "unknown"
        and not analysis.belongs_to_active_case
        and analysis.message_type in ("workflow_switch", "new_complaint")
    )

    if is_mismatch_switch:
        # Genuine workflow switch detected — start a fresh case
        logger.info(
            "[CustomerChat] Workflow mismatch guardrail: %s → %s "
            "(msg_type=%s, belongs_to_case=%s) — switching case",
            ctx.selected_workflow, analysis.workflow_hint,
            analysis.message_type, analysis.belongs_to_active_case,
        )
        ctx = _start_fresh_case_for_switch(ctx, req.message)
        case_context = {
            "selected_workflow": "", "service_type": "",
            "awaiting_field": "", "has_active_case": False,
        }
        # Do NOT override workflow_hint — keep the analyzer's detected workflow
    elif ctx.selected_workflow and analysis.message_type not in ("new_complaint", "workflow_switch"):
        if analysis.workflow_hint != ctx.selected_workflow:
            logger.info(
                "[CustomerChat] Overriding workflow_hint=%s with active "
                "case workflow=%s",
                analysis.workflow_hint, ctx.selected_workflow,
            )
        analysis.workflow_hint = ctx.selected_workflow

    # ── Debug trace (safe — no PIN/OTP/password/pin_hash) ──
    logger.info(
        "[CustomerChat] Analysis: type=%s, conf=%.2f, workflow=%s, emotion=%s",
        analysis.message_type, analysis.confidence,
        analysis.workflow_hint, analysis.customer_emotion,
    )
    logger.info(
        "[CustomerChat] Trace: case_id=%s, workflow=%s, session=%s, "
        "extracted.amount=%s, extracted.time=%s, extracted.bank=%s, "
        "missing_before=%s, missing_after=%s",
        ctx.case_id or "(none)",
        ctx.selected_workflow or analysis.workflow_hint or "(none)",
        req.session_id or "(none)",
        analysis.extracted.amount,
        analysis.extracted.approximate_time_text,
        analysis.extracted.bank_name,
        missing_before,
        missing_after,
    )

    # Track latest emotion for the back-office handoff summary
    ctx.customer_emotion = analysis.customer_emotion or ctx.customer_emotion

    # Populate trace: analysis
    trace.populate_analysis(analysis)
    trace.case_context.missing_fields_after = list(ctx.missing_fields)
    trace.case_context.extracted_info_after = dict(ctx.extracted_info)

    # ── Step 3e1.5: Thank-you / acknowledgement with active case ──
    # When customer says "được rồi", "ok", "cảm ơn", "tôi hiểu rồi" and
    # there IS an active case, we must NOT reset, NOT show greeting, NOT
    # re-run the resolver, and NOT create a new case/ticket.
    # Just respond with a short case-aware acknowledgement.
    if analysis.message_type == "thank_you" and ctx.selected_workflow:
        from fintech_agent.llm.response_composer import compose_acknowledgement_response
        ack_reply = compose_acknowledgement_response(
            resolution_status=ctx.extracted_info.get("resolution_status", ""),
            workflow=ctx.selected_workflow,
        )
        _record_turn(ctx, "agent", ack_reply)
        if req.session_id:
            _session_context[req.session_id] = ctx
        logger.info(
            "[CustomerChat] thank_you with active case '%s' — short ack (no reset, no ticket)",
            ctx.selected_workflow,
        )
        return CustomerChatResponse(
            public_case_id=ctx.case_id or "pending",
            status="received",
            public_response=ack_reply,
            missing_info_questions=[],
        )

    # ── Step 3e2: Off-topic / greeting → answer in scope, do NOT force a workflow ──
    # These are not complaints, so we must not ask for transaction_id, create a
    # case, or funnel them into the active workflow. Keeps the bot from replying
    # about "nạp tiền"/"vé" to "1+1?" or "kể chuyện cười".
    # thank_you WITHOUT an active case also falls here (normal greeting allowed).
    if analysis.message_type in NON_CASE_MESSAGE_TYPES or (
        analysis.message_type == "thank_you" and not ctx.selected_workflow
    ):
        reply = _build_off_topic_reply(analysis.message_type, session, policy)
        _record_turn(ctx, "agent", reply)
        if req.session_id:
            _session_context[req.session_id] = ctx
        logger.info(
            "[CustomerChat] Off-topic message_type=%s — in-scope redirect (no case)",
            analysis.message_type,
        )
        return CustomerChatResponse(
            public_case_id=ctx.case_id or "pending",
            status="received",
            public_response=reply,
            missing_info_questions=[],
        )

    # ── Step 3f: Explicit staff-support request → back-office handoff ──
    # Customer-initiated handoff. Creates/updates ONE ticket (deduped) and
    # tells the customer staff will follow up. No internal data is exposed.
    if session is not None and req.session_id and _STAFF_REQUEST_RE.search(req.message):
        reply = (
            "Chúng tôi đã chuyển yêu cầu của bạn tới nhân viên hỗ trợ. "
            "Bộ phận phụ trách sẽ liên hệ và xử lý theo quy trình. "
            "Vui lòng không gửi mã PIN, OTP hoặc mật khẩu."
        )
        _record_turn(ctx, "agent", reply)
        try:
            finalize_customer_chat_and_handoff(
                session, ctx, reason="staff_request",
                needs_more_info=bool(ctx.missing_fields),
            )
        except Exception as exc:
            logger.error("[CustomerChat] Handoff (staff_request) failed: %s", exc)
        _session_context[req.session_id] = ctx
        return CustomerChatResponse(
            public_case_id=ctx.case_id or "pending",
            status="received",
            public_response=reply,
            missing_info_questions=[],
        )

    # ── Step 4: Sensitive info guard ──
    if analysis.message_type == "provide_sensitive_info":
        warning = policy.get("sensitive_info_warning", "")
        if not warning:
            warning = (
                "⚠️ Vui lòng KHÔNG gửi mã PIN, OTP, mật khẩu "
                "hoặc số thẻ đầy đủ qua chat."
            )
        _record_turn(ctx, "agent", warning)
        if req.session_id:
            _session_context[req.session_id] = ctx
        return CustomerChatResponse(
            public_case_id=ctx.case_id or "pending",
            status="need_more_info",
            public_response=warning,
            missing_info_questions=[],
        )

    # ── Step 5: Ownership validation ──
    if session is not None:
        subject_type = session.get("subject_type", "")

        if subject_type == "wallet_user":
            ownership_error = _validate_wallet_user_ownership(
                req.message, session,
            )
            if ownership_error:
                return CustomerChatResponse(
                    public_case_id="",
                    status="received",
                    public_response=ownership_error,
                    missing_info_questions=[],
                )

        elif subject_type == "merchant":
            identity_error = _validate_merchant_identity(
                req.message, session,
            )
            if identity_error:
                return CustomerChatResponse(
                    public_case_id="",
                    status="received",
                    public_response=identity_error,
                    missing_info_questions=[],
                )

    # ── Step 6: Generic resolver ──
    resolution = resolve_case_evidence(session, case_context, analysis)

    # If resolver resolved, persist into context
    if resolution.resolved_entity_id and req.session_id:
        ctx.resolved_entity_id = resolution.resolved_entity_id
        ctx.extracted_info["transaction_id"] = resolution.resolved_entity_id
        if "transaction_id" in ctx.missing_fields:
            ctx.missing_fields.remove("transaction_id")

    # Subtract already-provided fields from resolver's missing_info
    resolution.missing_info = _subtract_provided_fields(
        resolution.missing_info, ctx.extracted_info,
    )

    # ── Step 6b: Build verified evidence + detect contradictions ──
    # Evidence binding: verified facts come ONLY from the resolver's verified_*
    # fields (DB/tool data for the logged-in account) — never from customer
    # claims. amount_mismatch also binds evidence (the verified record exists;
    # the CLAIM doesn't match it).
    if (
        resolution.resolution_status in ("resolved", "amount_mismatch")
        and resolution.resolved_entity_id
    ):
        ctx._verified_evidence = VerifiedEvidence(
            resolved_entity_id=resolution.resolved_entity_id or "",
            resolved_entity_type=resolution.resolved_entity_type,
            verified_amount=resolution.verified_amount,
            verified_status=resolution.verified_status,
            verified_owner_id=resolution.verified_owner_id,
            verified_bank_name=resolution.verified_bank_name,
            verified_bank_status=resolution.verified_bank_status,
            verified_provider_status=resolution.verified_provider_status,
            evidence_source="transaction_table",
        )
        ctx._contradictions = detect_contradictions(
            ctx._customer_claims, ctx._verified_evidence,
        )
        if ctx._contradictions:
            logger.info(
                "[CustomerChat] Detected %d contradiction(s): %s",
                len(ctx._contradictions),
                [(c.field, c.customer_claim, c.verified_value) for c in ctx._contradictions],
            )

    # ── Step 6b2: Amount claim conflicts with verified evidence ──
    # Two triggers, both data-driven:
    #   a) resolver fallback returned amount_mismatch (claimed amount matched
    #      nothing, but a verified problematic record exists on the account);
    #   b) the resolver verified a record (e.g. by stored id) and THIS turn's
    #      message claimed a different amount.
    # Reply deterministically from the dynamic claim/verified values — the old
    # diagnosis is stale for this claim and must not be reused.
    _amount_conflict = next(
        (c for c in (getattr(ctx, "_contradictions", []) or []) if c.field == "amount"),
        None,
    )
    if resolution.resolution_status == "amount_mismatch" or (
        resolution.resolution_status == "resolved"
        and _amount_conflict is not None
        and _turn_claimed_amount  # this turn actually (re)claimed an amount
    ):
        claimed_val = resolution.claimed_amount
        if claimed_val is None and _amount_conflict is not None:
            claimed_val = _amount_conflict.customer_claim
        verified_val = resolution.verified_amount

        # The previous diagnosis was built for a different claim context.
        ctx.last_diagnosis = {}

        wf_for_msg = ctx.selected_workflow or analysis.workflow_hint or ""
        reply = sanitize_customer_text(
            amount_mismatch_message(wf_for_msg, claimed_val, verified_val)
        )
        _record_turn(ctx, "agent", reply)
        if req.session_id:
            _session_context[req.session_id] = ctx
        logger.info(
            "[CustomerChat] amount_mismatch: claimed=%s verified=%s entity=%s "
            "— claim NOT merged into evidence, honest correction sent",
            claimed_val, verified_val, resolution.resolved_entity_id,
        )
        return CustomerChatResponse(
            public_case_id=ctx.case_id or "pending",
            status="need_more_info",
            public_response=reply,
            missing_info_questions=[],
        )

    logger.info(
        "[CustomerChat] Resolver: status=%s, entity=%s, id_exists=%s, "
        "candidates=%s, missing_after_subtract=%s",
        resolution.resolution_status,
        resolution.resolved_entity_type,
        bool(resolution.resolved_entity_id),
        getattr(resolution, '_candidate_count', '?'),
        resolution.missing_info,
    )

    # Populate trace: resolver
    trace.populate_resolver(resolution)
    trace.resolver.query_basis = {
        "has_transaction_id": bool(analysis.extracted.transaction_id),
        "has_amount": bool(analysis.extracted.amount),
        "has_time": bool(analysis.extracted.approximate_time_text),
        "has_bank": bool(analysis.extracted.bank_name),
        "has_reference": bool(analysis.extracted.bank_reference),
    }

    # ── Step 6e: Recheck context — compare current vs previous status ──
    # When the customer asks "khi nào nhận tiền?", "tình trạng sao rồi?",
    # or "kiểm tra lại giúp tôi" we re-query every time (the resolver
    # already does this). Here we detect whether the result changed so
    # the composer can say "status updated" vs "still the same".
    recheck_context: dict | None = None
    if (
        analysis.message_type in (
            "ask_status", "follow_up", "ask_what_happened",
            "ask_eta", "ask_what_to_do",
        )
        and resolution.resolution_status == "resolved"
        and ctx.last_diagnosis
    ):
        old_status = ctx.last_diagnosis.get("verified_status", "")
        new_status = resolution.verified_status or ""
        recheck_context = {
            "is_recheck": True,
            "status_changed": old_status != new_status and bool(old_status),
            "old_status": old_status,
            "new_status": new_status,
        }
        logger.info(
            "[CustomerChat] Recheck: old=%s new=%s changed=%s",
            old_status, new_status, recheck_context["status_changed"],
        )

    # ── Step 6c: No matching data on the logged-in account ──
    # The system could not find anything for this trusted session. Say so
    # clearly and immediately — never imply the transaction exists / is being
    # processed, and do not run the workflow or create a case for it.
    _ex = analysis.extracted
    _has_searchable = any([
        _ex.transaction_id, _ex.order_id, _ex.bill_code, _ex.amount,
        _ex.bank_name, _ex.bank_reference,
        _ex.approximate_time_text, _ex.approximate_date_text,
    ])
    prior_no_match = getattr(ctx, "_no_match_count", 0)
    is_no_match = resolution.resolution_status == "no_match"
    # Insistence: customer reasserts after a prior 'not found' but brings no new
    # searchable evidence and the system still cannot verify anything.
    is_insist = (
        prior_no_match >= 1
        and not _has_searchable
        and not resolution.resolved_entity_id
        and resolution.resolution_status in ("no_match", "need_more_info")
    )

    if is_no_match or is_insist:
        ctx._no_match_count = prior_no_match + 1
        # Account-lock no-match is about the ACCOUNT record, not a transaction —
        # repeating the transaction-insist wording would be the wrong domain.
        _is_account_no_match = (
            resolution.resolved_entity_type == "account"
            or (ctx.selected_workflow or analysis.workflow_hint) == "fraud_account_lock"
        )
        if is_insist or prior_no_match >= 1:
            reply = (
                no_lock_evidence_message() if _is_account_no_match
                else NO_MATCH_INSIST_RESPONSE
            )
        else:
            reply = resolution.public_response or (
                no_lock_evidence_message() if _is_account_no_match
                else NO_MATCH_INSIST_RESPONSE
            )
            reminder = policy.get("global_safety_reminder", "")
            if reminder and reminder.lower() not in reply.lower():
                reply = f"{reply} {reminder}".strip()

        reply = sanitize_customer_text(reply)
        _record_turn(ctx, "agent", reply)
        if req.session_id:
            _session_context[req.session_id] = ctx

        logger.info(
            "[CustomerChat] no_match/insist on session=%s (count=%d, status=%s) "
            "— honest 'not found' reply, no fake confirmation",
            req.session_id or "anonymous", ctx._no_match_count,
            resolution.resolution_status,
        )
        return CustomerChatResponse(
            public_case_id=ctx.case_id or "pending",
            status="need_more_info",
            public_response=reply,
            missing_info_questions=[],
        )

    # ── Step 6d: Several matching records → ask ONE narrowing field ──
    if resolution.resolution_status == "multiple_candidates" and resolution.public_response:
        reply = sanitize_customer_text(resolution.public_response)
        _record_turn(ctx, "agent", reply)
        if req.session_id:
            _session_context[req.session_id] = ctx
        logger.info(
            "[CustomerChat] multiple_candidates on session=%s — asked one narrowing field",
            req.session_id or "anonymous",
        )
        return CustomerChatResponse(
            public_case_id=ctx.case_id or "pending",
            status="need_more_info",
            public_response=reply,
            missing_info_questions=[],
        )

    # Customer eventually resolved → reset the no-match streak.
    if resolution.resolution_status == "resolved":
        ctx._no_match_count = 0

    # ── Step 7: If new complaint or no resolver match → run workflow ──
    state: dict | None = None
    if (
        analysis.message_type == "new_complaint"
        or (
            not ctx.case_id
            and analysis.message_type not in (
                "follow_up", "ask_status", "ask_eta",
                "ask_what_to_do", "provide_sensitive_info",
            )
        )
    ):
        # Build enriched complaint with identity
        if session is not None:
            enriched_complaint = _build_complaint_with_identity(
                req.message, session,
            )
            logger.info(
                "[CustomerChat] Session %s (%s) authenticated",
                req.session_id, session.get("subject_type"),
            )
        else:
            enriched_complaint = req.message

        try:
            svc = get_case_service()
            state = svc.create_and_run(raw_complaint=enriched_complaint)
        except Exception as exc:
            logger.error(
                "[CustomerChat] Internal error creating case: %s: %s",
                type(exc).__name__, exc,
            )
            fallback = policy.get("generic_fallback_response", "") or (
                "Chúng tôi đã ghi nhận khiếu nại của bạn. "
                "Bộ phận hỗ trợ sẽ kiểm tra và phản hồi trong thời gian sớm nhất."
            )
            return CustomerChatResponse(
                public_case_id="pending",
                status="received",
                public_response=fallback,
                missing_info_questions=[],
            )

    # ── Step 8: Public-safe evidence mapper ──
    raw_evidence = resolution.public_safe_evidence or {}
    rule_result = state.get("rule_decision") if state else None
    # Active case workflow wins; fall back to the per-message hint only when
    # there is no active case (e.g. a brand-new complaint).
    workflow = ctx.selected_workflow or (
        analysis.workflow_hint if analysis.workflow_hint != "unknown" else ""
    )
    if state is not None and state.get("selected_workflow"):
        graph_wf = state.get("selected_workflow")
        analyzer_wf = analysis.workflow_hint if analysis.workflow_hint != "unknown" else ""
        # RULE: The per-message analyzer's workflow_hint takes priority over
        # the graph's selected_workflow. The graph extracts from the enriched
        # complaint text which may contain profile metadata; the analyzer
        # extracts from the raw customer message only.
        # The graph should only override when the analyzer couldn't determine
        # the workflow (returned unknown/"").
        if analyzer_wf and analyzer_wf != graph_wf:
            logger.info(
                "[CustomerChat] Analyzer workflow (%s) overrides graph workflow (%s) "
                "— latest customer message determines routing",
                analyzer_wf, graph_wf,
            )
            workflow = analyzer_wf
        else:
            workflow = graph_wf
        # Re-run the resolver if the workflow changed from what was initially set.
        if (
            workflow
            and workflow != analysis.workflow_hint
            and resolution.resolution_status != "resolved"
        ):
            analysis.workflow_hint = workflow
            resolution = resolve_case_evidence(session, case_context, analysis)
            raw_evidence = resolution.public_safe_evidence or {}

    public_evidence = to_public_safe_evidence(
        raw_evidence=raw_evidence,
        rule_result=rule_result,
        workflow=workflow,
        policy=policy,
        resolution_status=resolution.resolution_status,
        missing_info=resolution.missing_info,
    )

    # ── Step 8b: Workflow identity assertion ──
    # Hard check: if the analysis says fraud_account_lock but the composition
    # workflow drifted to wallet_topup (or vice versa), re-run the resolver
    # for the correct workflow. This catches edge cases where the state graph
    # overrides the workflow to something the analyzer didn't intend.
    _analysis_wf = analysis.workflow_hint or ""
    if (
        _analysis_wf
        and _analysis_wf != "unknown"
        and workflow
        and workflow != _analysis_wf
        and state is None  # only for follow-up path; new-complaint path trusts the graph
    ):
        logger.warning(
            "[CustomerChat] Workflow identity mismatch at composition: "
            "analysis=%s, composition=%s — re-running resolver",
            _analysis_wf, workflow,
        )
        workflow = _analysis_wf
        resolution = resolve_case_evidence(session, case_context, analysis)
        raw_evidence = resolution.public_safe_evidence or {}
        public_evidence = to_public_safe_evidence(
            raw_evidence=raw_evidence,
            rule_result=rule_result,
            workflow=workflow,
            policy=policy,
            resolution_status=resolution.resolution_status,
            missing_info=resolution.missing_info,
        )

    # Populate trace: diagnosis
    trace.populate_diagnosis(public_evidence)
    trace.rule_engine.called = rule_result is not None
    trace.rule_engine.decision_present = bool(rule_result)

    # ── Step 9: Response composer ──
    # For new complaints with a workflow state, use the workflow response
    if state is not None:
        case_id = state.get("case_id", "pending")
        internal_status = state.get("status", CaseStatus.NEW)
        workflow_response = _extract_public_response(state)

        # Extract missing fields from workflow state
        missing_fields: list[str] = []
        ei = state.get("extracted_info")
        if ei is not None:
            if hasattr(ei, "missing_fields"):
                missing_fields = list(ei.missing_fields or [])
            elif isinstance(ei, dict):
                missing_fields = list(ei.get("missing_fields", []))

        safe_questions = compose_contextual_questions(
            missing_fields=missing_fields,
            extracted_info=dict(ctx.extracted_info),
            workflow=workflow,
            public_safe_diagnosis=public_evidence,
            customer_message=req.message,
            session=session,
        )

        # If the proactive account scan already found and diagnosed the
        # problematic transaction, answer the customer from THAT diagnosis —
        # don't fall back to a generic "đang xác định" or interrogate them.
        proactively_resolved = (
            resolution.resolution_status == "resolved"
            and bool(public_evidence.get("customer_safe_cause"))
        )

        customer_status = _map_to_customer_status(internal_status)
        if proactively_resolved:
            safe_questions = []           # nothing to ask — we already found it
            customer_status = "processing"
        elif safe_questions and customer_status != "need_more_info":
            customer_status = "need_more_info"

        if proactively_resolved:
            composed = compose_customer_response(
                customer_message=req.message,
                message_analysis=analysis,
                active_case_context=case_context,
                public_safe_evidence=public_evidence,
                resolution_status=resolution.resolution_status,
                contradictions=getattr(ctx, "_contradictions", None),
            )
            guardrail = check_response_safety(
                composed.public_message, policy,
                workflow=workflow, diagnosis=public_evidence,
            )
            final_response = (
                composed.public_message
                if guardrail.is_safe
                else (guardrail.sanitized_text or composed.public_message)
            )
        # Run guardrail on workflow response (workflow-aware).
        # The workflow's customer_reply_draft is STAFF-oriented and can leak
        # internal action wording (e.g. "manual payout draft") or overpromise.
        # If it is unsafe, we do NOT fall back to a fixed phrase — we re-compose
        # the reply from the public-safe diagnosis, then re-check it.
        elif (guardrail := check_response_safety(
            workflow_response, policy,
            workflow=workflow, diagnosis=public_evidence,
        )).is_safe:
            final_response = workflow_response
        else:
            logger.warning(
                "[CustomerChat] Workflow reply unsafe (%d violations) — "
                "re-composing from public-safe diagnosis",
                len(guardrail.violations),
            )
            recomposed = compose_customer_response(
                customer_message=req.message,
                message_analysis=analysis,
                active_case_context=case_context,
                public_safe_evidence=public_evidence,
                resolution_status=resolution.resolution_status,
                contradictions=getattr(ctx, '_contradictions', None),
            )
            guardrail = check_response_safety(
                recomposed.public_message, policy,
                workflow=workflow, diagnosis=public_evidence,
            )
            final_response = (
                recomposed.public_message
                if guardrail.is_safe
                else (guardrail.sanitized_text or recomposed.public_message)
            )

        # Strip any backend field names before the text reaches the customer.
        final_response = sanitize_customer_text(final_response)
        final_response = _apply_evidence_grounding(final_response, resolution, ctx, workflow)
        safe_questions = [sanitize_customer_text(q) for q in safe_questions]

        logger.info(
            "[CustomerChat] New case %s. status=%s, questions=%d",
            case_id, customer_status, len(safe_questions),
        )

        # Record agent reply in the (carried-forward) transcript
        _record_turn(ctx, "agent", final_response)

        # Track context (carry transcript/problem/emotion forward for handoff)
        if req.session_id and case_id and case_id != "pending":
            selected_wf = state.get("selected_workflow", "")
            _session_context[req.session_id] = ActiveCaseContext(
                case_id=case_id,
                selected_workflow=selected_wf,
                service_type=state.get("service_type", "") or "",
                missing_fields=list(missing_fields),
                last_public_response=final_response,
                extracted_info=dict(ctx.extracted_info),
                resolved_entity_id=ctx.resolved_entity_id,
                last_diagnosis=dict(public_evidence) if public_evidence else {},
                transcript=list(ctx.transcript),
                customer_problem=ctx.customer_problem,
                customer_emotion=ctx.customer_emotion,
                subject_type=ctx.subject_type,
            )
        elif req.session_id:
            _session_context[req.session_id] = ctx

        # ── Proactive back-office ticket creation ──
        # Create/update a ChatTicket when the case warrants staff attention.
        # Do NOT create tickets for off-topic, greeting, or fully-resolved-in-chat cases.
        if session is not None and req.session_id and case_id and case_id != "pending":
            _proactive_ctx = _session_context.get(req.session_id)
            if _proactive_ctx is not None and _should_create_ticket(
                analysis, resolution, state, _proactive_ctx,
            ):
                try:
                    finalize_customer_chat_and_handoff(
                        session, _proactive_ctx,
                        reason="chat_active",
                        needs_more_info=bool(missing_fields),
                        case_state=state if isinstance(state, dict) else None,
                    )
                except Exception as exc:
                    logger.error("[CustomerChat] Proactive ticket creation failed: %s", exc)

        # Populate trace for workflow path
        trace.populate_guardrail(guardrail)
        trace.active_case_id_after = case_id
        trace.final_status = customer_status
        trace.final_response_length = len(final_response)
        trace.questions_count = len(safe_questions)
        trace.log_safe()

        wf_debug_trace = None
        if req.debug and _is_dev:
            wf_debug_trace = trace.to_dict()

        return CustomerChatResponse(
            public_case_id=case_id,
            status=customer_status,
            public_response=final_response,
            missing_info_questions=safe_questions,
            debug_trace=wf_debug_trace,
        )

    # For an ongoing case, when this turn resolved no NEW evidence, reuse the
    # diagnosis built when the case was created. This keeps follow-ups specific
    # (e.g. "tiền giải ngân kẹt ở đâu?" answers from the settlement diagnosis)
    # instead of degrading to a generic "đang kiểm tra".
    #
    # GUARD: Only reuse if the diagnosis workflow matches the current workflow.
    # After a workflow switch, last_diagnosis may contain data from the OLD
    # workflow (e.g. wallet_topup diagnosis when the customer now has a
    # fraud_account_lock complaint). Reusing it would produce wrong wording.
    _diag_wf = (ctx.last_diagnosis or {}).get("workflow", "")
    _current_wf = analysis.workflow_hint or ""
    # EVIDENCE-BINDING GUARD: the old diagnosis is bound to the verified
    # transaction it was built from. If the customer's accumulated amount claim
    # now conflicts with that bound verified amount, the diagnosis is stale for
    # this claim — never reuse it (the claim must NOT inherit verified facts).
    _bound_amount = getattr(
        getattr(ctx, "_verified_evidence", None), "verified_amount", None,
    )
    _claim_amount = analysis.extracted.amount
    _claim_conflicts_binding = (
        _bound_amount is not None
        and _claim_amount is not None
        and int(_claim_amount) != int(_bound_amount)
    )
    if (
        state is None
        and ctx.last_diagnosis
        and resolution.resolution_status != "resolved"
        and not _claim_conflicts_binding
        and (_diag_wf == _current_wf or not _current_wf or _current_wf == "unknown")
    ):
        public_evidence = dict(ctx.last_diagnosis)
        workflow = public_evidence.get("workflow") or workflow
        trace.populate_diagnosis(public_evidence)
    elif (
        state is None
        and ctx.last_diagnosis
        and resolution.resolution_status != "resolved"
        and (_diag_wf != _current_wf or _claim_conflicts_binding)
    ):
        # Stale diagnosis (different workflow, or claim now conflicts with the
        # bound verified amount) — discard and let the resolver's fresh result
        # (even if thin) drive the response.
        logger.info(
            "[CustomerChat] Discarding stale last_diagnosis: "
            "diag_wf=%s, current_wf=%s, claim_conflicts_binding=%s",
            _diag_wf, _current_wf, _claim_conflicts_binding,
        )

    # For follow-ups and resolver-handled cases
    composed = compose_customer_response(
        customer_message=req.message,
        message_analysis=analysis,
        active_case_context=case_context,
        public_safe_evidence=public_evidence,
        resolution_status=resolution.resolution_status,
        contradictions=getattr(ctx, '_contradictions', None),
        recheck_context=recheck_context,
    )

    # ── Step 10: Output guardrail (workflow-aware) ──
    guardrail = check_response_safety(
        composed.public_message, policy,
        workflow=workflow, diagnosis=public_evidence,
    )
    final_message = (
        guardrail.sanitized_text
        if not guardrail.is_safe
        else composed.public_message
    )

    if not guardrail.is_safe:
        logger.warning(
            "[CustomerChat] Guardrail caught %d violations",
            len(guardrail.violations),
        )

    # Build safe questions from resolver missing info (already subtracted)
    safe_questions = compose_contextual_questions(
        missing_fields=resolution.missing_info,
        extracted_info=dict(ctx.extracted_info),
        workflow=workflow,
        public_safe_diagnosis=public_evidence,
        customer_message=req.message,
        session=session,
    )

    # If resolver resolved AND no remaining questions, clear the card
    if resolution.resolution_status == "resolved" and not safe_questions:
        safe_questions = []

    # Strip any backend field names before the text reaches the customer.
    final_message = sanitize_customer_text(final_message)
    final_message = _apply_evidence_grounding(final_message, resolution, ctx, workflow)
    safe_questions = [sanitize_customer_text(q) for q in safe_questions]

    # Determine status
    if safe_questions or composed.needs_more_info or resolution.resolution_status in (
        "need_more_info", "multiple_candidates", "no_match",
    ):
        customer_status = "need_more_info"
    elif resolution.resolution_status == "resolved":
        customer_status = "processing"
    else:
        customer_status = "received"

    # Record agent reply in the conversation transcript (for handoff)
    _record_turn(ctx, "agent", final_message)

    # Update context — always save back (preserves extracted_info)
    if req.session_id:
        ctx.last_public_response = final_message
        # If this turn produced a fresh, specific diagnosis, remember it.
        if resolution.resolution_status == "resolved" and public_evidence.get(
            "customer_safe_cause",
        ):
            ctx.last_diagnosis = dict(public_evidence)
        _session_context[req.session_id] = ctx

    logger.info(
        "[CustomerChat] Follow-up response: type=%s, status=%s, "
        "guardrail_safe=%s, session=%s, questions=%d",
        analysis.message_type, customer_status,
        guardrail.is_safe, req.session_id or "anonymous",
        len(safe_questions),
    )


    # Populate trace: composer + guardrail + final
    trace.response_composer.called = True
    trace.response_composer.input_has_diagnosis = bool(
        public_evidence.get("customer_safe_cause"),
    )
    trace.response_composer.input_has_evidence = bool(
        resolution.public_safe_evidence,
    )
    trace.populate_guardrail(guardrail)
    trace.active_case_id_after = ctx.case_id
    trace.final_status = customer_status
    trace.final_response_length = len(final_message)
    trace.questions_count = len(safe_questions)

    # Log trace in dev mode
    trace.log_safe()

    # Return trace in debug mode (dev only)
    debug_trace_dict = None
    if req.debug and _is_dev:
        debug_trace_dict = trace.to_dict()

    return CustomerChatResponse(
        public_case_id=ctx.case_id or "pending",
        status=customer_status,
        public_response=final_message,
        missing_info_questions=safe_questions,
        debug_trace=debug_trace_dict,
    )


# ─── Chat-end / TTL-expiry handoff ─────────────────────────────

class ChatHandoffRequest(BaseModel):
    """Finalize a chat into a back-office ticket."""
    session_id: str = Field(..., min_length=1, max_length=200)
    reason: str = Field(
        default="ended",
        description="ended | expired | staff_request",
    )


class ChatHandoffPublicResponse(BaseModel):
    """Safe handoff acknowledgement — NO internal identity/case data."""
    handed_off: bool = False
    public_case_ref: str = ""
    message: str = ""


@router.post(
    "/customer-chat/handoff",
    response_model=ChatHandoffPublicResponse,
    status_code=201,
    summary="Finalize a customer chat into a back-office ticket",
    description=(
        "Called when the chat ends or its session expires. Creates/updates ONE "
        "back-office ticket (deduped). Returns only a safe acknowledgement."
    ),
)
async def customer_chat_handoff(req: ChatHandoffRequest) -> ChatHandoffPublicResponse:
    """Finalize a chat → back-office ticket. Idempotent per chat (deduped)."""
    reason = req.reason if req.reason in ("ended", "expired", "staff_request") else "ended"

    ctx = _session_context.get(req.session_id)
    if ctx is None:
        # Fallback: proactive creation may have already made a ticket before
        # context was lost (process restart, TTL). Update it and return success.
        store = get_ticket_store()
        existing = store.get_by_case_key(f"chat:{req.session_id}")
        if existing:
            existing.handoff_reason = reason
            existing.touch()
            store.upsert(f"chat:{req.session_id}", existing)
            logger.info(
                "[Handoff] Updated proactive ticket %s (ctx lost, reason=%s)",
                existing.ticket_id, reason,
            )
            return ChatHandoffPublicResponse(
                handed_off=True,
                public_case_ref=existing.public_case_ref,
                message=(
                    "Yêu cầu của bạn đã được chuyển tới bộ phận hỗ trợ. "
                    "Chúng tôi sẽ phản hồi trong thời gian sớm nhất."
                ),
            )
        # No context and no existing ticket — quick close with no activity.
        return ChatHandoffPublicResponse(
            handed_off=False,
            message="Không có hội thoại để chuyển tiếp.",
        )

    session: dict | None = None
    try:
        session = get_mock_session_repo().get_session(req.session_id)
    except Exception as exc:
        logger.error("[Handoff] Failed to load session: %s", exc)

    try:
        ticket = finalize_customer_chat_and_handoff(
            session, ctx, reason=reason,
            needs_more_info=bool(ctx.missing_fields),
        )
    except Exception as exc:
        logger.error("[Handoff] finalize failed: %s", exc)
        return ChatHandoffPublicResponse(
            handed_off=False,
            message="Hệ thống đang bận. Vui lòng thử lại sau.",
        )

    return ChatHandoffPublicResponse(
        handed_off=True,
        public_case_ref=ticket.public_case_ref,
        message=(
            "Yêu cầu của bạn đã được chuyển tới bộ phận hỗ trợ. "
            "Chúng tôi sẽ phản hồi trong thời gian sớm nhất."
        ),
    )
