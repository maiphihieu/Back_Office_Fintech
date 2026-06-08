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


# ─── Safe Missing-Info Question Mapping ─────────────────────────

_SAFE_QUESTION_MAP: dict[str, str] = {
    "transaction_id": (
        "Bạn có thể gửi mã giao dịch, hoặc thời gian giao dịch gần đúng, "
        "số tiền và ngân hàng đã trừ tiền."
    ),
    "user_id": "Vui lòng cung cấp tên tài khoản hoặc số điện thoại đã đăng ký ví.",
    "phone": "Vui lòng cung cấp số điện thoại đã đăng ký ví.",
    "email": "Vui lòng cung cấp email đã đăng ký ví.",
    "merchant_id": "Vui lòng cung cấp mã đối tác (Merchant ID).",
    "merchant_name": "Vui lòng cung cấp tên đối tác/merchant.",
    "tax_code": "Vui lòng cung cấp mã số thuế.",
    "payout_id": "Vui lòng cung cấp mã thanh toán (Payout ID).",
    "batch_id": "Vui lòng cung cấp mã lô thanh toán (Batch ID).",
    "order_id": "Vui lòng cung cấp mã đơn hàng.",
    "bill_code": "Vui lòng cung cấp mã hóa đơn.",
    "customer_code": "Vui lòng cung cấp mã khách hàng.",
    "service_type": "Vui lòng cho biết loại dịch vụ bạn đang sử dụng.",
    "transaction_time": "Vui lòng cho biết thời gian giao dịch gần đúng.",
    "amount": "Vui lòng cho biết số tiền giao dịch.",
    "bank_name": "Vui lòng cho biết ngân hàng bạn đã dùng.",
    "bank_reference": "Vui lòng cung cấp mã tham chiếu ngân hàng.",
}

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
    ActiveCaseContext,
    ExtractedFields,
    MessageAnalysis,
    analyze_customer_message,
    load_response_policy,
)
from fintech_agent.api.generic_resolver import (
    ResolutionResult,
    resolve_case_evidence,
)
from fintech_agent.safety.evidence_mapper import to_public_safe_evidence
from fintech_agent.llm.response_composer import compose_customer_response
from fintech_agent.safety.output_guardrail import check_response_safety
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


# ─── Helper Functions ───────────────────────────────────────────

def _build_safe_questions(
    missing_fields: list[str],
    session: dict | None = None,
) -> list[str]:
    """Convert internal missing field names to safe customer questions."""
    known_fields: frozenset[str] = frozenset()
    if session is not None:
        subject_type = session.get("subject_type", "")
        if subject_type == "wallet_user":
            known_fields = _WALLET_USER_KNOWN_FIELDS
        elif subject_type == "merchant":
            known_fields = _MERCHANT_KNOWN_FIELDS

    questions: list[str] = []
    for field_name in missing_fields:
        lower = field_name.lower()
        if any(blocked in lower for blocked in _BLOCKED_QUESTION_FIELDS):
            continue
        if field_name in known_fields:
            continue
        question = _SAFE_QUESTION_MAP.get(field_name)
        if question:
            questions.append(question)
    return questions


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
            ("phone", "SĐT"), ("email", "Email"), ("display_name", "Tên"),
        ]:
            val = session.get(key)
            if val:
                parts.append(f"[{label}: {val}]")

    elif subject_type == "merchant":
        for key, label in [
            ("merchant_id", "Merchant ID"), ("tax_code", "MST"),
            ("phone", "SĐT"), ("email", "Email"), ("display_name", "Tên"),
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

    # ── Step 2: Load active case context ──
    ctx = (
        _session_context.get(req.session_id, ActiveCaseContext())
        if req.session_id
        else ActiveCaseContext()
    )

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

    # ── Step 3b: Persist extracted fields into active case context ──
    # Accumulate across messages — don't discard previous extractions.
    _merge_extracted_into_context(ctx, analysis.extracted)

    # ── Step 3c: Merge accumulated context back into analysis.extracted ──
    # So resolver sees ALL info gathered across messages, not just this one.
    _apply_context_to_extraction(ctx, analysis.extracted)

    # ── Step 3d: Recalculate missing_fields after extraction ──
    missing_before = list(ctx.missing_fields)
    _recalculate_missing_fields(ctx)
    missing_after = list(ctx.missing_fields)

    # ── Step 3e: Active case workflow is AUTHORITATIVE ──
    # For anything that is not a brand-new complaint, the active case's
    # selected_workflow drives evidence lookup, diagnosis, and guardrail —
    # NOT the per-message workflow_hint (which the LLM may guess wrong, e.g.
    # classifying a train_ticket follow-up as wallet_topup).
    if ctx.selected_workflow and analysis.message_type != "new_complaint":
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
        workflow = state.get("selected_workflow")

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

        safe_questions = _build_safe_questions(missing_fields, session=session)

        customer_status = _map_to_customer_status(internal_status)
        if safe_questions and customer_status != "need_more_info":
            customer_status = "need_more_info"

        # Run guardrail on workflow response (workflow-aware).
        # The workflow's customer_reply_draft is STAFF-oriented and can leak
        # internal action wording (e.g. "manual payout draft") or overpromise.
        # If it is unsafe, we do NOT fall back to a fixed phrase — we re-compose
        # the reply from the public-safe diagnosis, then re-check it.
        guardrail = check_response_safety(
            workflow_response, policy,
            workflow=workflow, diagnosis=public_evidence,
        )
        if guardrail.is_safe:
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
        # Create/update a ChatTicket immediately after the case is built so the
        # dashboard is populated even if the /handoff endpoint is never called
        # (process restart, TTL expiry with lost context, etc.).
        if session is not None and req.session_id and case_id and case_id != "pending":
            _proactive_ctx = _session_context.get(req.session_id)
            if _proactive_ctx is not None:
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
    if (
        state is None
        and ctx.last_diagnosis
        and resolution.resolution_status != "resolved"
    ):
        public_evidence = dict(ctx.last_diagnosis)
        workflow = public_evidence.get("workflow") or workflow
        trace.populate_diagnosis(public_evidence)

    # For follow-ups and resolver-handled cases
    composed = compose_customer_response(
        customer_message=req.message,
        message_analysis=analysis,
        active_case_context=case_context,
        public_safe_evidence=public_evidence,
        resolution_status=resolution.resolution_status,
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
    safe_questions = _build_safe_questions(
        resolution.missing_info, session=session,
    )

    # If resolver resolved AND no remaining questions, clear the card
    if resolution.resolution_status == "resolved" and not safe_questions:
        safe_questions = []

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
