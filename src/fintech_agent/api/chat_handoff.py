"""Customer-chat → back-office handoff.

Creates/updates ONE back-office ticket when a customer chat ends, expires, or
the customer requests staff support. Exposes a back-office dashboard API to
list/filter/search those tickets and a detail view.

Pipeline (finalize):
  session context
  → build complainant_info (trusted, staff-only)
  → summarize conversation (redacted)
  → public_safe_diagnosis (from active case context)
  → staff evidence summary (from case state, public-safe)
  → create OR update ticket (dedup by customer_chat_case_id)
  → set back-office status

SECURITY:
  - One ticket per chat (dedup) — never duplicates.
  - Complainant identity is BACK-OFFICE ONLY (separate API, separate models).
  - PIN/OTP/password/card are redacted before storage.
  - No refund/force-success/unlock/manual-payout is executed here.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from fintech_agent.safety.redaction import redact_sensitive
from fintech_agent.schemas.chat_handoff import (
    SOURCE_CUSTOMER_CHAT,
    TICKET_CLOSED_NO_ACTION,
    TICKET_NEED_MORE_INFO,
    TICKET_PENDING_APPROVAL,
    TICKET_PENDING_REVIEW,
    ChatHandoffTicket,
    ChatMessageRecord,
    ComplainantInfo,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backoffice", tags=["backoffice-chat-tickets"])


# ─── In-memory ticket store (MVP, consistent with CaseService) ──

class ChatTicketStore:
    """In-memory store of handoff tickets, deduped by chat case id."""

    def __init__(self) -> None:
        self._tickets: dict[str, ChatHandoffTicket] = {}
        self._by_case: dict[str, str] = {}  # dedup key → ticket_id

    def get_by_case_key(self, case_key: str) -> ChatHandoffTicket | None:
        tid = self._by_case.get(case_key)
        return self._tickets.get(tid) if tid else None

    def get(self, ticket_id: str) -> ChatHandoffTicket | None:
        return self._tickets.get(ticket_id)

    def upsert(self, case_key: str, ticket: ChatHandoffTicket) -> ChatHandoffTicket:
        self._tickets[ticket.ticket_id] = ticket
        self._by_case[case_key] = ticket.ticket_id
        return ticket

    def list_all(self) -> list[ChatHandoffTicket]:
        return list(self._tickets.values())

    def clear(self) -> None:
        self._tickets.clear()
        self._by_case.clear()


_store: ChatTicketStore | None = None


def get_ticket_store() -> ChatTicketStore:
    global _store
    if _store is None:
        _store = ChatTicketStore()
    return _store


def reset_ticket_store() -> None:
    global _store
    _store = None


# ─── Complainant mapping (trusted session → staff-only identity) ──

def build_complainant_info(session: dict | None) -> ComplainantInfo:
    """Map a trusted server-side session to complainant identity.

    Pulls only fields that are present. Never invents IDs.
    """
    if not session:
        return ComplainantInfo()

    subject_type = session.get("subject_type", "") or ""
    info = ComplainantInfo(
        subject_type=subject_type,
        display_name=session.get("display_name", "") or "",
        phone=session.get("phone", "") or "",
        email=session.get("email", "") or "",
    )

    if subject_type == "wallet_user":
        info.user_id = session.get("user_id", "") or ""
        info.wallet_id = session.get("wallet_id", "") or ""
        info.account_status = session.get("account_status", "") or ""
        info.wallet_status = (
            session.get("wallet_status")
            or session.get("withdrawal_status")
            or ""
        )
    elif subject_type == "merchant":
        info.merchant_name = (
            session.get("merchant_name") or session.get("display_name") or ""
        )
        info.merchant_id = session.get("merchant_id", "") or ""
        info.tax_code = session.get("tax_code", "") or ""
        info.bank_account_status = session.get("bank_account_status", "") or ""
        info.settlement_cycle = session.get("settlement_cycle", "") or ""

    return info


# ─── Placeholder label detection ─────────────────────────────────
#
# The LLM field extractor may return generic labels (e.g. "số tiền giao dịch")
# instead of actual values when information is absent or parsing fails.
# These must be filtered out before storage and rendering.

_PLACEHOLDER_LABELS: frozenset[str] = frozenset({
    # Exact values from _PROVIDED_LABELS (the reverse side)
    "số tiền giao dịch",
    "ngân hàng",
    "mã tham chiếu ngân hàng",
    "thời gian giao dịch",
    "ngày giao dịch",
    "mã giao dịch",
    "mã đơn hàng",
    "mã hóa đơn",
    "mã khách hàng",
    "nhà cung cấp",
    # Bare generic labels (value == the field name itself)
    "số tiền",
    "thời gian",
    "ngày",
    "thời gian giao dịch gần đúng",
    "tên ngân hàng",
    "mã tham chiếu",
    "mã thanh toán",
    "mã lô thanh toán",
    "payout id",
    "payout_id",
    "merchant id",
    "merchant_id",
    "user id",
    "user_id",
    "wallet id",
    "wallet_id",
    # Generic null-equivalents
    "unknown",
    "none",
    "null",
    "n/a",
    "na",
    "không rõ",
    "chưa rõ",
    "chưa xác định",
    "transaction id",
    "transaction_id",
    "bank name",
    "bank_name",
    "amount",
})


def _is_placeholder_label(value: object) -> bool:
    """Return True if value is null-like or a generic label instead of a real value."""
    if value is None:
        return True
    s = str(value).strip().lower()
    if not s:
        return True
    return s in _PLACEHOLDER_LABELS


def _fmt_amount(amount_val: object) -> str:
    """Format an extracted amount as a human-readable string like '500.000đ'."""
    if amount_val is None or _is_placeholder_label(amount_val):
        return ""
    try:
        return f"{int(float(str(amount_val))):,}đ".replace(",", ".")
    except (ValueError, TypeError):
        return str(amount_val)


# ─── Conversation summary (redacted, deterministic) ─────────────

# Public-safe labels for info the customer provided (from extracted_info keys).
_PROVIDED_LABELS: dict[str, str] = {
    "amount": "số tiền giao dịch",
    "bank_name": "ngân hàng",
    "bank_reference": "mã tham chiếu ngân hàng",
    "approximate_time_text": "thời gian giao dịch",
    "approximate_date_text": "ngày giao dịch",
    "transaction_id": "mã giao dịch",
    "order_id": "mã đơn hàng",
    "bill_code": "mã hóa đơn",
    "customer_code": "mã khách hàng",
    "provider_name": "nhà cung cấp",
}


def summarize_conversation(ctx: Any) -> dict:
    """Build a redacted conversation summary from the active case context."""
    transcript: list[dict] = list(getattr(ctx, "transcript", []) or [])

    customer_msgs = [m for m in transcript if m.get("role") == "customer"]
    first_problem = redact_sensitive(
        getattr(ctx, "customer_problem", "")
        or (customer_msgs[0]["text"] if customer_msgs else "")
    )
    latest = redact_sensitive(customer_msgs[-1]["text"]) if customer_msgs else ""

    extracted = getattr(ctx, "extracted_info", {}) or {}

    # customer_provided_info: human-readable labels of what was provided
    # (used as a fallback summary when actual extracted_info is available)
    provided: list[str] = []
    for key, val in extracted.items():
        if val and key in _PROVIDED_LABELS and not _is_placeholder_label(val):
            provided.append(_PROVIDED_LABELS[key])

    # safe_extracted: actual values, placeholder labels filtered out
    # These carry real data (e.g. amount=500000, bank_name="Vietcombank")
    # for the staff ticket. Never expose to the customer chat frontend.
    safe_extracted: dict = {}
    for key, val in extracted.items():
        if val is None or _is_placeholder_label(val):
            continue
        # Normalize numeric amounts to int so the UI/diagnosis format consistently
        if key == "amount" and isinstance(val, str):
            digits = val.strip().replace(".", "").replace(",", "").replace("đ", "").replace("vnd", "")
            if digits.isdigit():
                val = int(digits)
        safe_extracted[key] = val
    if safe_extracted:
        logger.debug(
            "[Handoff] summarize_conversation extracted %d real fields: %s",
            len(safe_extracted), list(safe_extracted.keys()),
        )

    timeline = [
        ChatMessageRecord(
            role=m.get("role", ""),
            text=redact_sensitive(m.get("text", "")),
            timestamp=m.get("timestamp", ""),
        )
        for m in transcript
    ]

    diagnosis = getattr(ctx, "last_diagnosis", {}) or {}
    cause = diagnosis.get("customer_safe_cause", "") or ""
    summary_parts = [p for p in (first_problem, cause) if p]

    return {
        "customer_problem": first_problem,
        "latest_customer_message": latest,
        "customer_emotion": getattr(ctx, "customer_emotion", "neutral") or "neutral",
        "key_customer_claims": diagnosis.get("confirmed_public_facts", []) or [],
        "customer_provided_info": provided,
        "extracted_info": safe_extracted,
        "conversation_summary": redact_sensitive(" — ".join(summary_parts)),
        "timeline": timeline,
    }


# ─── Staff evidence summary (public-safe, from case state) ──────

def _build_staff_evidence_summary(case_state: dict | None, diagnosis: dict) -> dict:
    """Public-safe evidence summary for staff (no raw sensitive values)."""
    summary: dict = {
        "what_was_checked": diagnosis.get("what_was_checked", []),
        "confirmed_public_facts": diagnosis.get("confirmed_public_facts", []),
        "likely_issue_location": diagnosis.get("likely_issue_location", ""),
    }
    if not case_state:
        return summary

    rd = case_state.get("rule_decision")
    if isinstance(rd, dict):
        # diagnosis text from rule engine is staff-facing context
        summary["rule_diagnosis"] = rd.get("diagnosis", "")
    return summary


def _recommended_action(case_state: dict | None) -> tuple[str, bool, str]:
    """Extract (recommended_action, approval_required, risk_level) from state."""
    if not case_state:
        return "", False, "unknown"
    action = ""
    rd = case_state.get("rule_decision")
    if isinstance(rd, dict):
        action = rd.get("action", "") or ""
    approval = bool(case_state.get("approval_required", False))
    risk = case_state.get("risk_level") or "unknown"
    if hasattr(risk, "value"):
        risk = risk.value
    return action, approval, str(risk)


def _resolve_status(
    approval_required: bool,
    recommended_action: str,
    needs_more_info: bool,
) -> str:
    """Map case signals to a back-office ticket status."""
    if approval_required:
        return TICKET_PENDING_APPROVAL
    if recommended_action in ("no_action", "wait_sla", ""):
        if needs_more_info:
            return TICKET_NEED_MORE_INFO
        # nothing actionable and nothing missing → closed
        return TICKET_CLOSED_NO_ACTION if recommended_action == "no_action" else TICKET_PENDING_REVIEW
    if needs_more_info:
        return TICKET_NEED_MORE_INFO
    return TICKET_PENDING_REVIEW


# ─── Finalize (create or update — never duplicate) ─────────────

def finalize_customer_chat_and_handoff(
    session: dict | None,
    ctx: Any,
    reason: str = "ended",
    needs_more_info: bool = False,
    case_state: dict | None = None,
) -> ChatHandoffTicket:
    """Create or update ONE back-office ticket for this chat.

    Dedup key = ctx.case_id if present, else the session id. Calling this twice
    for the same chat updates the existing ticket instead of creating a new one.

    Args:
        session: Trusted server-side session dict (identity source).
        ctx: ActiveCaseContext (workflow, diagnosis, transcript).
        reason: ended | expired | staff_request.
        needs_more_info: whether the chat ended awaiting customer info.
        case_state: optional case state for staff evidence/recommended action.
    """
    store = get_ticket_store()

    session_id = (session or {}).get("session_id", "") if session else ""
    chat_case_id = getattr(ctx, "case_id", "") or ""
    dedup_key = chat_case_id or f"chat:{session_id}"

    # Pull case state if we have a case id and none was provided
    if case_state is None and chat_case_id:
        try:
            from fintech_agent.api.service import get_case_service
            state = get_case_service().get_case(chat_case_id)
            case_state = dict(state) if state else None
        except Exception as exc:
            logger.warning("[Handoff] Could not load case %s: %s", chat_case_id, exc)
            case_state = None

    complainant = build_complainant_info(session)
    convo = summarize_conversation(ctx)
    diagnosis = getattr(ctx, "last_diagnosis", {}) or {}
    action, approval, risk = _recommended_action(case_state)
    status = _resolve_status(approval, action, needs_more_info)

    existing = store.get_by_case_key(dedup_key)
    ticket = existing or ChatHandoffTicket(
        ticket_id=f"CHT_{uuid.uuid4().hex[:10].upper()}",
    )

    ticket.source = SOURCE_CUSTOMER_CHAT
    ticket.customer_chat_case_id = chat_case_id
    ticket.customer_session_id = session_id
    ticket.public_case_ref = chat_case_id or getattr(ctx, "case_id", "") or ""
    ticket.subject_type = complainant.subject_type or getattr(ctx, "subject_type", "")
    ticket.complainant = complainant

    ticket.conversation_summary = convo["conversation_summary"]
    ticket.customer_problem = convo["customer_problem"]
    ticket.customer_emotion = convo["customer_emotion"]
    ticket.key_customer_claims = convo["key_customer_claims"]
    ticket.customer_provided_info = convo["customer_provided_info"]
    ticket.extracted_info = convo.get("extracted_info", {})
    ticket.latest_customer_message = convo["latest_customer_message"]
    ticket.timeline = convo["timeline"]

    ticket.selected_workflow = getattr(ctx, "selected_workflow", "") or diagnosis.get("workflow", "")
    ticket.issue_type = diagnosis.get("situation", "") or ""
    ticket.public_safe_diagnosis = dict(diagnosis)
    ticket.diagnosis_confidence = diagnosis.get("confidence", "low")

    ticket.internal_staff_evidence_summary = _build_staff_evidence_summary(
        case_state, diagnosis,
    )
    ticket.recommended_action = action
    ticket.approval_required = approval
    ticket.risk_level = risk
    ticket.backoffice_ticket_status = status
    ticket.handoff_reason = reason

    # ── Populate claims vs evidence data from ctx ──
    _claims = getattr(ctx, "_customer_claims", None)
    if _claims and hasattr(_claims, "to_summary"):
        ticket.customer_claims_data = _claims.to_summary()

    _evidence = getattr(ctx, "_verified_evidence", None)
    if _evidence and hasattr(_evidence, "to_summary"):
        ticket.verified_evidence_data = _evidence.to_summary()

    _contradictions = getattr(ctx, "_contradictions", None)
    if _contradictions:
        ticket.contradictions_data = [
            {
                "field": getattr(c, "field", ""),
                "customer_claim": getattr(c, "customer_claim", None),
                "verified_value": getattr(c, "verified_value", None),
                "severity": getattr(c, "severity", "medium"),
            }
            for c in _contradictions
        ]

    ticket.touch()

    store.upsert(dedup_key, ticket)
    logger.info(
        "[Handoff] %s ticket %s (case=%s, subject=%s, wf=%s, status=%s, reason=%s)",
        "Updated" if existing else "Created",
        ticket.ticket_id, dedup_key, ticket.subject_type,
        ticket.selected_workflow, status, reason,
    )
    return ticket


# ─── Back-office API: list / filter / search / detail / decide ──

class ComplainantPublic(BaseModel):
    """Complainant info for BACK-OFFICE staff (not the customer frontend)."""
    subject_type: str = ""
    display_name: str = ""
    phone: str = ""
    email: str = ""
    user_id: str = ""
    wallet_id: str = ""
    account_status: str = ""
    wallet_status: str = ""
    merchant_name: str = ""
    merchant_id: str = ""
    tax_code: str = ""
    bank_account_status: str = ""
    settlement_cycle: str = ""


# ─── New structured models for action-oriented staff UX ──────────

class AgentDiagnosisPublic(BaseModel):
    """Staff-readable agent diagnosis."""
    what_was_checked: list[str] = Field(default_factory=list)
    confirmed_facts: list[str] = Field(default_factory=list)
    money_or_issue_location: str = ""
    likely_bottleneck: str = ""
    confidence: str = "low"
    why_staff_action_needed: str = ""
    missing_evidence: list[str] = Field(default_factory=list)


class EvidenceCheckItem(BaseModel):
    """One item in the evidence checklist."""
    label: str
    status: str = "missing"   # checked | missing | needs_review
    detail: str = ""


class StaffAction(BaseModel):
    """Normalized staff_action_contract for the action panel."""
    # ── Core identity ──
    action_title: str = ""
    action_type: str = ""
    approval_required: bool = False
    next_owner_team: str = ""
    why_recommended: str = ""
    approve_button_label: str = ""
    # ── Effect / safety ──
    approve_effect: list[str] = Field(default_factory=list)
    approve_does_not_do: list[str] = Field(default_factory=list)
    preconditions_checked: list[str] = Field(default_factory=list)
    missing_preconditions: list[str] = Field(default_factory=list)
    safety_warnings: list[str] = Field(default_factory=list)
    next_status_after_approve: str = ""
    audit_event_type: str = ""
    # ── Legacy compat (kept for downstream) ──
    recommended_action_label: str = ""
    recommended_action_type: str = ""
    required_preconditions: list[str] = Field(default_factory=list)


class TicketHeaderPublic(BaseModel):
    """Compact ticket header for the summary card."""
    ticket_id: str = ""
    source: str = ""
    selected_workflow: str = ""
    backoffice_ticket_status: str = ""
    risk_level: str = "unknown"
    approval_required: bool = False
    created_at: str = ""
    updated_at: str = ""
    summary: str = ""


class CustomerProblemPublic(BaseModel):
    """Structured customer problem info."""
    original_complaint: str = ""
    latest_customer_message: str = ""
    customer_emotion: str = "neutral"
    extracted_amount: str = ""
    extracted_time: str = ""
    extracted_bank_provider: str = ""
    extracted_transaction_id: str = ""
    extracted_order_id: str = ""
    extracted_bill_code: str = ""
    extracted_payout_id: str = ""


class AuditLogEntry(BaseModel):
    """One audit log entry."""
    actor: str = ""
    action: str = ""
    timestamp: str = ""
    comment: str = ""


class ChatTicketRow(BaseModel):
    """Dashboard row (summary)."""
    ticket_id: str
    source: str
    subject_type: str
    complainant_display_name: str = ""
    complainant_phone: str = ""
    complainant_email: str = ""
    complainant_user_id: str = ""
    complainant_merchant_id: str = ""
    selected_workflow: str = ""
    issue_type: str = ""
    risk_level: str = "unknown"
    approval_required: bool = False
    recommended_action: str = ""
    backoffice_ticket_status: str = ""
    created_at: str = ""
    updated_at: str = ""


class ChatTicketListResponse(BaseModel):
    tickets: list[ChatTicketRow]
    total: int


class ChatMessagePublic(BaseModel):
    role: str
    text: str
    timestamp: str = ""


class ChatTicketDetail(BaseModel):
    """Full ticket detail for the staff ticket page."""
    ticket_id: str
    source: str
    customer_chat_case_id: str = ""
    public_case_ref: str = ""
    subject_type: str = ""
    complainant: ComplainantPublic
    # conversation
    conversation_summary: str = ""
    customer_problem: str = ""
    customer_emotion: str = "neutral"
    key_customer_claims: list[str] = Field(default_factory=list)
    customer_provided_info: list[str] = Field(default_factory=list)
    latest_customer_message: str = ""
    timeline: list[ChatMessagePublic] = Field(default_factory=list)
    # diagnosis
    selected_workflow: str = ""
    issue_type: str = ""
    public_safe_diagnosis: dict = Field(default_factory=dict)
    diagnosis_confidence: str = "low"
    # evidence + action
    internal_staff_evidence_summary: dict = Field(default_factory=dict)
    recommended_action: str = ""
    approval_required: bool = False
    risk_level: str = "unknown"
    linked_action_draft_id: str = ""
    backoffice_ticket_status: str = ""
    handoff_reason: str = ""
    created_at: str = ""
    updated_at: str = ""
    # ── Actual extracted values (real amounts/times/banks, never labels) ──
    extracted_info: dict = Field(default_factory=dict)
    # ── Investigation result (explicit, staff-facing) ──
    resolved_entity: dict = Field(default_factory=dict)   # {type, id}
    money_or_issue_location: str = ""
    missing_evidence: list[str] = Field(default_factory=list)
    # ── NEW structured fields for action-oriented UX ──
    ticket_header: TicketHeaderPublic | None = None
    customer_problem_structured: CustomerProblemPublic | None = None
    agent_diagnosis: AgentDiagnosisPublic | None = None
    evidence_checklist: list[EvidenceCheckItem] = Field(default_factory=list)
    staff_action: StaffAction | None = None
    conversation_timeline: list[ChatMessagePublic] = Field(default_factory=list)
    audit_entries: list[AuditLogEntry] = Field(default_factory=list)
    # ── Claims vs Evidence (data-driven, for staff review) ──
    customer_claims_data: list[dict] = Field(default_factory=list)
    verified_evidence_data: list[dict] = Field(default_factory=list)
    contradictions_data: list[dict] = Field(default_factory=list)


class TicketDecisionRequest(BaseModel):
    actor: str = Field(..., min_length=1)
    comment: str | None = None


# ═══════════════════════════════════════════════════════════════════
# ACTION CONTRACTS — workflow-aware, never hard-coded per customer
# ═══════════════════════════════════════════════════════════════════
#
# Each contract defines the *semantics* of what approve/review means
# for that action type.  Button labels are conditional on
# approval_required (true → "Phê duyệt …" ; false → "Xác nhận …").
# ═══════════════════════════════════════════════════════════════════

from dataclasses import dataclass as _dc
from typing import Sequence as _Seq


@_dc(frozen=True)
class _ActionContract:
    """Immutable contract template for one action type."""
    action_title: str
    team: str
    btn_approval: str          # button when approval_required=True
    btn_no_approval: str       # button when approval_required=False
    effects: _Seq[str]
    does_not_do: _Seq[str]
    audit_event: str


# ── Shared safety denials (always appended) ──
_SHARED_DENIALS: list[str] = [
    "Không tự động cập nhật số dư ví",
    "Không tự động chuyển tiền",
    "Không chỉnh sửa sổ cái (ledger)",
]

_ACTION_CONTRACTS: dict[str, _ActionContract] = {
    # ── wallet_topup ──
    "create_force_success_draft": _ActionContract(
        action_title="Tạo draft kiểm tra/cập nhật giao dịch nạp tiền",
        team="CS/Ops",
        btn_approval="Phê duyệt tạo draft xử lý",
        btn_no_approval="Xác nhận đã review",
        effects=[
            "Tạo hoặc cập nhật draft xử lý giao dịch cho team CS/Ops",
            "Chuyển ticket sang trạng thái chờ xử lý",
            "Ghi audit log",
        ],
        does_not_do=[
            "Không trực tiếp cộng tiền vào ví",
            "Không chỉnh sửa ledger",
            "Không tự động force-success",
        ],
        audit_event="approve_force_success_draft",
    ),
    "create_reconciliation_ticket_draft": _ActionContract(
        action_title="Tạo draft kiểm tra/cập nhật giao dịch nạp tiền",
        team="CS/Ops",
        btn_approval="Phê duyệt tạo draft xử lý",
        btn_no_approval="Xác nhận đã review",
        effects=[
            "Tạo hoặc cập nhật draft xử lý giao dịch cho team CS/Ops",
            "Chuyển ticket sang trạng thái chờ xử lý",
            "Ghi audit log",
        ],
        does_not_do=[
            "Không trực tiếp cộng tiền vào ví",
            "Không chỉnh sửa ledger",
            "Không tự động force-success",
        ],
        audit_event="approve_reconciliation_draft",
    ),
    # ── refund ──
    "create_refund_request_draft": _ActionContract(
        action_title="Tạo draft yêu cầu hoàn tiền",
        team="CS/Ops",
        btn_approval="Phê duyệt tạo draft hoàn tiền",
        btn_no_approval="Xác nhận đã review",
        effects=[
            "Tạo draft yêu cầu hoàn tiền cho team CS/Ops",
            "Chuyển ticket sang trạng thái chờ xử lý",
            "Ghi audit log",
        ],
        does_not_do=[
            "Không tự động hoàn tiền",
            "Không trực tiếp cộng tiền vào ví",
            "Không tự phát hành vé",
        ],
        audit_event="approve_refund_draft",
    ),
    # ── merchant settlement ──
    "create_manual_payout_draft": _ActionContract(
        action_title="Chuyển Settlement/Ops review khoản giải ngân",
        team="Settlement/Ops",
        btn_approval="Phê duyệt chuyển Settlement/Ops",
        btn_no_approval="Chuyển bước xử lý",
        effects=[
            "Tạo draft giải ngân cho team Settlement/Ops review",
            "Gán ticket cho team Settlement/Ops",
            "Chuyển ticket sang trạng thái chờ xử lý",
            "Ghi audit log",
        ],
        does_not_do=[
            "Không tự động chuyển tiền",
            "Không tự sửa tài khoản ngân hàng merchant",
            "Không đảm bảo payout ngay lập tức",
        ],
        audit_event="approve_manual_payout_draft",
    ),
    # ── fraud / account lock ──
    "create_unlock_account_draft": _ActionContract(
        action_title="Chuyển Risk/Ops review tài khoản bị khóa",
        team="Risk/Ops",
        btn_approval="Phê duyệt chuyển Risk/Ops",
        btn_no_approval="Chuyển bước xử lý",
        effects=[
            "Tạo draft review tài khoản cho team Risk/Ops",
            "Gán ticket cho team Risk/Ops",
            "Chuyển ticket sang trạng thái chờ xử lý",
            "Ghi audit log",
        ],
        does_not_do=[
            "Không tự mở khóa tài khoản",
            "Không bỏ qua bước xác minh",
        ],
        audit_event="approve_unlock_draft",
    ),
    # ── manual review / fallback ──
    "manual_review": _ActionContract(
        action_title="Chuyển nhân viên xử lý thủ công",
        team="CS/Ops",
        btn_approval="Phê duyệt chuyển Ops review",
        btn_no_approval="Chuyển bước xử lý",
        effects=[
            "Gán ticket cho nhân viên xử lý thủ công",
            "Chuyển ticket sang trạng thái chờ xử lý",
            "Ghi audit log",
        ],
        does_not_do=[],
        audit_event="approve_manual_review",
    ),
    # ── customer response (no approval needed) ──
    "draft_customer_response": _ActionContract(
        action_title="Soạn trả lời khách hàng",
        team="CS/Ops",
        btn_approval="Xác nhận đã review",
        btn_no_approval="Xác nhận đã review",
        effects=[
            "Cập nhật trạng thái ticket",
            "Lưu quyết định của nhân viên",
            "Ghi audit log",
        ],
        does_not_do=[],
        audit_event="confirm_customer_response",
    ),
    # ── document request ──
    "create_request_documents_response_draft": _ActionContract(
        action_title="Yêu cầu khách bổ sung giấy tờ",
        team="CS/Ops",
        btn_approval="Xác nhận đã review",
        btn_no_approval="Xác nhận đã review",
        effects=[
            "Cập nhật trạng thái ticket",
            "Lưu quyết định của nhân viên",
            "Ghi audit log",
        ],
        does_not_do=[],
        audit_event="confirm_document_request",
    ),
    # ── merchant corrections ──
    "request_bank_account_correction": _ActionContract(
        action_title="Yêu cầu Merchant cập nhật TK ngân hàng",
        team="CS/Ops",
        btn_approval="Xác nhận đã review",
        btn_no_approval="Xác nhận đã review",
        effects=[
            "Cập nhật trạng thái ticket",
            "Lưu quyết định của nhân viên",
            "Ghi audit log",
        ],
        does_not_do=[
            "Không thay đổi thông tin ngân hàng merchant",
        ],
        audit_event="confirm_bank_correction",
    ),
    "request_identity_correction": _ActionContract(
        action_title="Yêu cầu Merchant cập nhật thông tin",
        team="CS/Ops",
        btn_approval="Xác nhận đã review",
        btn_no_approval="Xác nhận đã review",
        effects=[
            "Cập nhật trạng thái ticket",
            "Lưu quyết định của nhân viên",
            "Ghi audit log",
        ],
        does_not_do=[],
        audit_event="confirm_identity_correction",
    ),
    "send_unc_email_draft": _ActionContract(
        action_title="Gửi UNC/mã tham chiếu cho Merchant",
        team="CS/Ops",
        btn_approval="Xác nhận đã review",
        btn_no_approval="Xác nhận đã review",
        effects=[
            "Cập nhật trạng thái ticket",
            "Lưu quyết định của nhân viên",
            "Ghi audit log",
        ],
        does_not_do=[],
        audit_event="confirm_unc_email",
    ),
    # ── no-op ──
    "no_action": _ActionContract(
        action_title="Không cần thao tác thêm",
        team="CS/Ops",
        btn_approval="Đóng ticket không cần phê duyệt",
        btn_no_approval="Đóng ticket không cần phê duyệt",
        effects=[
            "Cập nhật trạng thái ticket sang đã xử lý",
            "Ghi audit log",
        ],
        does_not_do=[],
        audit_event="close_no_action",
    ),
    "wait_sla": _ActionContract(
        action_title="Chờ SLA — chưa cần hành động",
        team="CS/Ops",
        btn_approval="Xác nhận đã review",
        btn_no_approval="Xác nhận đã review",
        effects=[
            "Cập nhật trạng thái ticket",
            "Ghi audit log",
        ],
        does_not_do=[],
        audit_event="confirm_wait_sla",
    ),
}

# Workflow-specific "does not do" overrides, keyed by selected_workflow
_WORKFLOW_DENIALS: dict[str, list[str]] = {
    "wallet_topup": [
        "Không trực tiếp cộng tiền vào ví",
        "Không chỉnh sửa ledger",
        "Không tự động force-success",
    ],
    "train_ticket": [
        "Không tự hoàn tiền",
        "Không tự phát hành vé",
    ],
    "utility_bill": [
        "Không tự hoàn tiền",
        "Không chỉnh sửa ledger",
    ],
    "fraud_account_lock": [
        "Không tự mở khóa tài khoản",
        "Không bỏ qua bước xác minh",
    ],
    "merchant_settlement_delay": [
        "Không tự chuyển tiền",
        "Không tự sửa tài khoản ngân hàng merchant",
        "Không đảm bảo payout ngay lập tức",
    ],
}

_DEFAULT_CONTRACT = _ActionContract(
    action_title="Xử lý ticket",
    team="CS/Ops",
    btn_approval="Phê duyệt bước xử lý",
    btn_no_approval="Chuyển bước xử lý",
    effects=[
        "Cập nhật trạng thái ticket sang bước tiếp theo",
        "Lưu quyết định của nhân viên",
        "Ghi audit log",
    ],
    does_not_do=[],
    audit_event="approve_generic",
)


# ─── Builder functions (pure, no side effects) ───────────────────

def _build_ticket_header(t: ChatHandoffTicket) -> TicketHeaderPublic:
    return TicketHeaderPublic(
        ticket_id=t.ticket_id,
        source=t.source,
        selected_workflow=t.selected_workflow,
        backoffice_ticket_status=t.backoffice_ticket_status,
        risk_level=t.risk_level,
        approval_required=t.approval_required,
        created_at=t.created_at,
        updated_at=t.updated_at,
        summary=t.conversation_summary or t.customer_problem or "",
    )


def _build_customer_problem(t: ChatHandoffTicket) -> CustomerProblemPublic:
    """Extract structured problem info from actual extracted_info values.

    Uses t.extracted_info (real values) as the authoritative source.
    Placeholder labels are never rendered — they are filtered at storage time.
    """
    ei = t.extracted_info or {}

    def _str(key: str) -> str:
        v = ei.get(key)
        return str(v) if v is not None and not _is_placeholder_label(v) else ""

    return CustomerProblemPublic(
        original_complaint=t.customer_problem or "",
        latest_customer_message=t.latest_customer_message or "",
        customer_emotion=t.customer_emotion or "neutral",
        extracted_amount=_fmt_amount(ei.get("amount")),
        extracted_time=_str("approximate_time_text") or _str("approximate_date_text"),
        extracted_bank_provider=_str("bank_name") or _str("provider_name"),
        extracted_transaction_id=_str("transaction_id"),
        extracted_order_id=_str("order_id"),
        extracted_bill_code=_str("bill_code"),
        extracted_payout_id=_str("payout_id"),
    )


def _build_agent_diagnosis(t: ChatHandoffTicket) -> AgentDiagnosisPublic:
    """Reshape diagnosis data into staff-readable structure.

    Combines:
    - Customer claims (from actual extracted_info values)
    - System findings (from public_safe_diagnosis / internal_staff_evidence_summary)
    so staff sees both what the customer reported AND what the system found.
    """
    diag = t.public_safe_diagnosis or {}
    ev = t.internal_staff_evidence_summary or {}
    ei = t.extracted_info or {}

    what_checked: list[str] = list(ev.get("what_was_checked", []) or diag.get("what_was_checked", []) or [])
    system_confirmed: list[str] = list(
        diag.get("confirmed_public_facts", []) or ev.get("confirmed_public_facts", []) or []
    )

    # Build customer-claim facts from actual extracted values
    claims: list[str] = []
    amount_val = ei.get("amount")
    if amount_val is not None and not _is_placeholder_label(amount_val):
        claims.append(f"Khách cung cấp số tiền {_fmt_amount(amount_val)}")
    time_val = ei.get("approximate_time_text") or ei.get("approximate_date_text")
    if time_val and not _is_placeholder_label(time_val):
        claims.append(f"Thời gian khoảng {time_val}")
    bank_val = ei.get("bank_name") or ei.get("provider_name")
    if bank_val and not _is_placeholder_label(bank_val):
        claims.append(f"Ngân hàng: {bank_val}")
    txn_id = ei.get("transaction_id")
    if txn_id and not _is_placeholder_label(txn_id):
        claims.append(f"Mã giao dịch: {txn_id}")

    # Merge: claims first, then non-duplicate system findings
    claim_lower = {c.lower() for c in claims}
    all_confirmed = claims + [f for f in system_confirmed if f.lower() not in claim_lower]

    # Ensure what_was_checked is populated
    if not what_checked and claims:
        what_checked = [
            "Thông tin danh tính qua session",
            "Thông tin giao dịch do khách cung cấp",
        ]

    # "Tiền/vấn đề đang nằm ở đâu" — where the money/problem currently is.
    money_location = (
        ev.get("money_or_issue_location", "")
        or ev.get("likely_issue_location", "")
        or diag.get("likely_issue_location", "")
        or diag.get("customer_safe_cause", "")
    )
    # "Likely bottleneck" — the stuck processing step (from the investigation,
    # falling back to the money-location finding when not investigated).
    bottleneck = ev.get("likely_bottleneck", "") or money_location
    confidence = t.diagnosis_confidence or diag.get("confidence", "low")
    missing_evidence = list(ev.get("missing_evidence", []) or [])

    rule_diag = ev.get("rule_diagnosis", "")
    why_needed = rule_diag or money_location
    if not why_needed and t.issue_type:
        why_needed = f"Loại vấn đề: {t.issue_type}"
    if not why_needed and claims:
        why_needed = "Đã có thông tin từ khách. Cần nhân viên kiểm tra và tạo draft xử lý."
    if not why_needed:
        why_needed = "Chưa xác định đầy đủ — cần kiểm tra thêm"

    return AgentDiagnosisPublic(
        what_was_checked=what_checked,
        confirmed_facts=all_confirmed,
        money_or_issue_location=str(money_location),
        likely_bottleneck=str(bottleneck) if bottleneck else "Chưa xác định đầy đủ — cần kiểm tra thêm",
        confidence=str(confidence),
        why_staff_action_needed=str(why_needed),
        missing_evidence=missing_evidence,
    )


def _build_evidence_checklist(t: ChatHandoffTicket) -> list[EvidenceCheckItem]:
    """Produce checklist cards from diagnosis + evidence data.

    When an investigation has run, its per-source `evidence_summary` (each item
    marked checked/missing) is authoritative — staff see exactly which data
    sources were queried and which are missing.
    """
    diag = t.public_safe_diagnosis or {}
    ev = t.internal_staff_evidence_summary or {}
    items: list[EvidenceCheckItem] = []

    # ── Investigation path: explicit per-source checklist ──
    evidence_summary = ev.get("evidence_summary")
    if isinstance(evidence_summary, list) and evidence_summary:
        for row in evidence_summary:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status", "missing"))
            if status not in ("checked", "missing", "needs_review"):
                status = "missing"
            items.append(EvidenceCheckItem(
                label=str(row.get("label", "")),
                status=status,
                detail=str(row.get("detail", "")),
            ))
        # Confirmed facts add concrete detail beyond the source checklist
        for fact in ev.get("confirmed_public_facts", []) or []:
            if not any(e.label == str(fact) for e in items):
                items.append(EvidenceCheckItem(label=str(fact), status="checked"))
        return items

    # ── Fallback (chat-time data, no investigation) ──
    what_checked = ev.get("what_was_checked", []) or diag.get("what_was_checked", [])
    if isinstance(what_checked, list):
        for item in what_checked:
            items.append(EvidenceCheckItem(label=str(item), status="checked"))
    elif what_checked:
        items.append(EvidenceCheckItem(label=str(what_checked), status="checked"))

    # Confirmed facts → checked items
    confirmed = ev.get("confirmed_public_facts", []) or diag.get("confirmed_public_facts", [])
    if isinstance(confirmed, list):
        for item in confirmed:
            if not any(e.label == str(item) for e in items):
                items.append(EvidenceCheckItem(label=str(item), status="checked"))

    # Likely issue → needs_review
    issue_loc = ev.get("likely_issue_location", "") or diag.get("likely_issue_location", "")
    if issue_loc and not any(e.label == str(issue_loc) for e in items):
        items.append(EvidenceCheckItem(label=str(issue_loc), status="needs_review", detail="Cần xem xét"))

    # If no items at all, add a missing marker
    if not items:
        items.append(EvidenceCheckItem(label="Chưa có bằng chứng", status="missing"))

    return items


def _build_staff_action(t: ChatHandoffTicket) -> StaffAction:
    """Build the normalized staff_action_contract from ticket data.

    Generated from: workflow, rule_result, recommended_action,
    approval_required, ticket status, action draft state.
    Never from exact customer message text.
    """
    action_type = t.recommended_action or ""
    contract = _ACTION_CONTRACTS.get(action_type, _DEFAULT_CONTRACT)

    # Button label is conditional on approval_required
    btn_label = contract.btn_approval if t.approval_required else contract.btn_no_approval

    # Build effects list — start from contract, always add safe defaults
    effects = list(contract.effects)
    if contract.team != "CS/Ops" and not any(contract.team in e for e in effects):
        effects.append(f"Gán ticket cho team {contract.team}")

    # Build does_not_do — contract-specific + workflow-specific + shared
    does_not_do = list(contract.does_not_do)
    # Add workflow-specific denials if not already covered
    wf_denials = _WORKFLOW_DENIALS.get(t.selected_workflow, [])
    for d in wf_denials:
        if d not in does_not_do:
            does_not_do.append(d)
    # Add shared denials if not already covered
    for d in _SHARED_DENIALS:
        if d not in does_not_do:
            does_not_do.append(d)

    # Why recommended — from diagnosis
    diag = t.public_safe_diagnosis or {}
    ev = t.internal_staff_evidence_summary or {}
    rule_diag = ev.get("rule_diagnosis", "")
    customer_cause = diag.get("customer_safe_cause", "")
    why = rule_diag or customer_cause
    if not why and t.issue_type:
        why = f"Loại vấn đề: {t.issue_type}"
    if not why:
        why = "Agent đã kiểm tra và đề xuất bước xử lý này"

    # Preconditions checked — prefer the investigation's concrete source list
    preconditions: list[str] = []
    missing: list[str] = []
    inv_checked = ev.get("what_was_checked") or diag.get("what_was_checked", [])
    inv_missing = ev.get("missing_evidence")
    if isinstance(inv_checked, list) and inv_checked:
        for label in inv_checked:
            preconditions.append(f"Đã kiểm tra: {label}")
    elif inv_checked:
        preconditions.append("Đã kiểm tra bằng chứng giao dịch")
    # Explicit missing-evidence from the investigation (says exactly what is missing)
    if isinstance(inv_missing, list) and inv_missing:
        for label in inv_missing:
            entry = f"Thiếu: {label}"
            if entry not in missing:
                missing.append(entry)
    elif not inv_checked:
        # No investigation data at all — generic fallback only as a last resort.
        missing.append("Chưa kiểm tra đầy đủ bằng chứng")
    if t.linked_action_draft_id:
        preconditions.append("Đã tạo action draft")

    # Safety warnings
    warnings: list[str] = []
    if t.risk_level in ("high", "critical"):
        warnings.append(f"Mức rủi ro: {t.risk_level.upper()}")
    if rule_diag and "conflict" in str(rule_diag).lower():
        warnings.append("Phát hiện xung đột dữ liệu")
    if missing:
        warnings.append("Một số điều kiện chưa được kiểm tra")

    return StaffAction(
        # Core contract
        action_title=contract.action_title,
        action_type=action_type,
        approval_required=t.approval_required,
        next_owner_team=contract.team,
        why_recommended=str(why),
        approve_button_label=btn_label,
        # Effects
        approve_effect=effects,
        approve_does_not_do=does_not_do,
        preconditions_checked=preconditions,
        missing_preconditions=missing,
        safety_warnings=warnings,
        next_status_after_approve="approved",
        audit_event_type=contract.audit_event,
        # Legacy compat
        recommended_action_label=contract.action_title,
        recommended_action_type=action_type,
        required_preconditions=preconditions,
    )


# ─── Investigation integration ───────────────────────────────────
#
# When staff opens a ticket, run a real, workflow-aware investigation
# (resolver → MCP evidence lookup → rule engine) and fold its data-driven
# results onto the ticket so the existing builders render a specific
# diagnosis, real evidence, and clear missing evidence.

def _has_resolvable_entity(t: ChatHandoffTicket) -> bool:
    """True when there is something to investigate.

    Transaction workflows can be investigated either from a direct id OR from
    the complainant identity plus searchable criteria (amount/bank/time) — the
    investigation resolver then pins down the transaction. This is why real
    tickets (where the chat never captured a transaction_id) still get a real,
    specific diagnosis instead of a vague placeholder.
    """
    wf = t.selected_workflow or ""
    ei = t.extracted_info or {}
    if wf in ("wallet_topup", "train_ticket", "utility_bill"):
        has_direct_id = not (_is_placeholder_label(ei.get("transaction_id"))
                             and _is_placeholder_label(ei.get("order_id")))
        if has_direct_id:
            return True
        # identity + at least one searchable criterion
        if not (t.complainant.user_id or "").strip():
            return False
        return any(not _is_placeholder_label(ei.get(k)) for k in (
            "amount", "bank_name", "bank_reference",
            "approximate_time_text", "approximate_date_text",
        ))
    if wf == "fraud_account_lock":
        return bool((t.complainant.user_id or "").strip()
                    or not _is_placeholder_label(ei.get("user_id")))
    if wf == "merchant_settlement_delay":
        return bool((t.complainant.merchant_id or "").strip()
                    or not _is_placeholder_label(ei.get("merchant_id")))
    return False


def _apply_investigation_to_ticket(t: ChatHandoffTicket, inv: Any) -> None:
    """Fold a TicketInvestigation onto the ticket (cache + feed builders).

    Only overrides diagnosis/action fields when the investigation actually
    resolved evidence; when unresolved it still records exactly what is missing.
    """
    ev = dict(t.internal_staff_evidence_summary or {})
    ev["evidence_summary"] = inv.evidence_summary
    ev["missing_evidence"] = inv.missing_evidence
    ev["resolved_entity"] = {
        "type": inv.resolved_entity_type, "id": inv.resolved_entity_id,
    }
    if inv.what_was_checked:
        ev["what_was_checked"] = inv.what_was_checked
    if inv.confirmed_facts:
        ev["confirmed_public_facts"] = inv.confirmed_facts
    if inv.likely_issue:
        ev["likely_issue_location"] = inv.likely_issue
    if inv.money_or_issue_location:
        ev["money_or_issue_location"] = inv.money_or_issue_location
    if inv.likely_bottleneck:
        ev["likely_bottleneck"] = inv.likely_bottleneck
    if inv.why_staff_action_needed:
        ev["rule_diagnosis"] = inv.why_staff_action_needed
    t.internal_staff_evidence_summary = ev

    t.diagnosis_confidence = inv.confidence or t.diagnosis_confidence

    if inv.resolved:
        if inv.rule_action:
            t.recommended_action = inv.rule_action
        t.approval_required = inv.approval_required
        if inv.risk_level and inv.risk_level != "unknown":
            t.risk_level = inv.risk_level
        # Enrich the staff-facing diagnosis (does not touch customer-safe chat copy)
        diag = dict(t.public_safe_diagnosis or {})
        if inv.confirmed_facts:
            diag["confirmed_public_facts"] = inv.confirmed_facts
        if inv.what_was_checked:
            diag["what_was_checked"] = inv.what_was_checked
        if inv.likely_issue:
            diag["likely_issue_location"] = inv.likely_issue
        t.public_safe_diagnosis = diag
    else:
        # Evidence insufficient → primary action must NOT be "Phê duyệt xử lý".
        # Route to manual review; staff can still "Yêu cầu bổ sung".
        t.recommended_action = "manual_review"
        t.approval_required = False
        diag = dict(t.public_safe_diagnosis or {})
        if inv.likely_issue:
            diag["likely_issue_location"] = inv.likely_issue
        t.public_safe_diagnosis = diag


def _maybe_investigate(t: ChatHandoffTicket) -> None:
    """Run the investigation when the ticket has something to investigate."""
    if not _has_resolvable_entity(t):
        return
    try:
        from fintech_agent.api.ticket_investigation import (
            investigate_customer_chat_ticket,
        )
        inv = investigate_customer_chat_ticket(t)
        _apply_investigation_to_ticket(t, inv)
    except Exception:  # noqa: BLE001 — never fail the detail endpoint
        logger.exception("[ChatTicket] Investigation failed for %s", t.ticket_id)


# ─── Row / Detail serializers ────────────────────────────────────

def _to_row(t: ChatHandoffTicket) -> ChatTicketRow:
    return ChatTicketRow(
        ticket_id=t.ticket_id,
        source=t.source,
        subject_type=t.subject_type,
        complainant_display_name=t.complainant.display_name,
        complainant_phone=t.complainant.phone,
        complainant_email=t.complainant.email,
        complainant_user_id=t.complainant.user_id,
        complainant_merchant_id=t.complainant.merchant_id,
        selected_workflow=t.selected_workflow,
        issue_type=t.issue_type,
        risk_level=t.risk_level,
        approval_required=t.approval_required,
        recommended_action=t.recommended_action,
        backoffice_ticket_status=t.backoffice_ticket_status,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _to_detail(t: ChatHandoffTicket) -> ChatTicketDetail:
    all_msgs = [ChatMessagePublic(**vars(m)) for m in t.timeline]
    # Conversation timeline: latest 5 by default
    timeline_limited = all_msgs[-5:] if len(all_msgs) > 5 else all_msgs

    return ChatTicketDetail(
        ticket_id=t.ticket_id,
        source=t.source,
        customer_chat_case_id=t.customer_chat_case_id,
        public_case_ref=t.public_case_ref,
        subject_type=t.subject_type,
        complainant=ComplainantPublic(**vars(t.complainant)),
        conversation_summary=t.conversation_summary,
        customer_problem=t.customer_problem,
        customer_emotion=t.customer_emotion,
        key_customer_claims=t.key_customer_claims,
        customer_provided_info=t.customer_provided_info,
        extracted_info=t.extracted_info,
        latest_customer_message=t.latest_customer_message,
        timeline=all_msgs,
        selected_workflow=t.selected_workflow,
        issue_type=t.issue_type,
        public_safe_diagnosis=t.public_safe_diagnosis,
        diagnosis_confidence=t.diagnosis_confidence,
        internal_staff_evidence_summary=t.internal_staff_evidence_summary,
        recommended_action=t.recommended_action,
        approval_required=t.approval_required,
        risk_level=t.risk_level,
        linked_action_draft_id=t.linked_action_draft_id,
        backoffice_ticket_status=t.backoffice_ticket_status,
        handoff_reason=t.handoff_reason,
        created_at=t.created_at,
        updated_at=t.updated_at,
        # Investigation result (explicit, staff-facing)
        resolved_entity=dict((t.internal_staff_evidence_summary or {}).get("resolved_entity", {})),
        money_or_issue_location=str((t.internal_staff_evidence_summary or {}).get("money_or_issue_location", "")),
        missing_evidence=list((t.internal_staff_evidence_summary or {}).get("missing_evidence", []) or []),
        # Structured fields for action-oriented staff UX
        ticket_header=_build_ticket_header(t),
        customer_problem_structured=_build_customer_problem(t),
        agent_diagnosis=_build_agent_diagnosis(t),
        evidence_checklist=_build_evidence_checklist(t),
        staff_action=_build_staff_action(t),
        conversation_timeline=timeline_limited,
        audit_entries=[AuditLogEntry(**e) for e in t.audit_log],
        # Claims vs Evidence
        customer_claims_data=t.customer_claims_data,
        verified_evidence_data=t.verified_evidence_data,
        contradictions_data=t.contradictions_data,
    )


def _matches_search(t: ChatHandoffTicket, q: str) -> bool:
    q = q.strip().lower()
    if not q:
        return True
    haystack = " ".join(str(x).lower() for x in [
        t.ticket_id, t.public_case_ref, t.customer_chat_case_id,
        t.complainant.phone, t.complainant.email,
        t.complainant.user_id, t.complainant.merchant_id,
        t.complainant.tax_code,
        *(t.customer_provided_info or []),
    ])
    return q in haystack


@router.get("/chat-tickets", response_model=ChatTicketListResponse)
async def list_chat_tickets(
    source: str = Query("customer_chat"),
    workflow: str | None = Query(None),
    status: str | None = Query(None),
    risk_level: str | None = Query(None),
    approval_required: bool | None = Query(None),
    subject_type: str | None = Query(None),
    created_from: str | None = Query(None),
    created_to: str | None = Query(None),
    assigned_team: str | None = Query(None),
    q: str | None = Query(None, description="Search phone/email/user_id/merchant_id/IDs"),
) -> ChatTicketListResponse:
    """List handoff tickets with filters + search (back-office only)."""
    store = get_ticket_store()
    rows = store.list_all()

    def keep(t: ChatHandoffTicket) -> bool:
        if source and source != "all" and t.source != source:
            return False
        if workflow and t.selected_workflow != workflow:
            return False
        if status and t.backoffice_ticket_status != status:
            return False
        if risk_level and t.risk_level != risk_level:
            return False
        if approval_required is not None and t.approval_required != approval_required:
            return False
        if subject_type and t.subject_type != subject_type:
            return False
        if assigned_team and t.assigned_team != assigned_team:
            return False
        if created_from and t.created_at < created_from:
            return False
        if created_to and t.created_at > created_to:
            return False
        if q and not _matches_search(t, q):
            return False
        return True

    filtered = [t for t in rows if keep(t)]
    filtered.sort(key=lambda t: t.updated_at, reverse=True)
    return ChatTicketListResponse(
        tickets=[_to_row(t) for t in filtered],
        total=len(filtered),
    )


@router.get("/chat-tickets/{ticket_id}", response_model=ChatTicketDetail)
async def get_chat_ticket(ticket_id: str) -> ChatTicketDetail:
    """Get full ticket detail (back-office only)."""
    t = get_ticket_store().get(ticket_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    # Run (or refresh) the back-office investigation when staff opens the ticket.
    _maybe_investigate(t)
    detail = _to_detail(t)
    logger.debug(
        "[ChatTicket] ticket=%s extracted_keys=%s evidence_present=%s "
        "rule_result_present=%s action_type=%s approval_required=%s",
        ticket_id,
        list(t.extracted_info.keys()) if t.extracted_info else [],
        bool(t.internal_staff_evidence_summary),
        bool((t.internal_staff_evidence_summary or {}).get("rule_diagnosis")),
        detail.staff_action.action_type if detail.staff_action else None,
        t.approval_required,
    )
    return detail


def _decide(
    ticket_id: str,
    new_status: str,
    actor: str = "",
    action_label: str = "",
    comment: str | None = None,
) -> ChatTicketDetail:
    t = get_ticket_store().get(ticket_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # Resolve the contract for audit_event_type and team assignment
    contract = _ACTION_CONTRACTS.get(t.recommended_action or "", _DEFAULT_CONTRACT)

    t.backoffice_ticket_status = new_status
    # Assign next owner team on approve
    if new_status == "approved" and contract.team:
        t.assigned_team = contract.team

    t.audit_log.append({
        "actor": actor,
        "action": action_label or new_status,
        "audit_event_type": contract.audit_event if new_status == "approved" else action_label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "comment": comment or "",
    })
    t.touch()
    return _to_detail(t)


@router.post("/chat-tickets/{ticket_id}/approve", response_model=ChatTicketDetail)
async def approve_chat_ticket(ticket_id: str, req: TicketDecisionRequest) -> ChatTicketDetail:
    """Mark ticket reviewed/approved. Does NOT execute any money movement.

    Safe actions only:
    - Update ticket status
    - Create/update action draft if applicable
    - Assign next owner team
    - Write audit log
    - Store staff decision
    """
    return _decide(ticket_id, "approved", actor=req.actor, action_label="approve", comment=req.comment)


@router.post("/chat-tickets/{ticket_id}/reject", response_model=ChatTicketDetail)
async def reject_chat_ticket(ticket_id: str, req: TicketDecisionRequest) -> ChatTicketDetail:
    return _decide(ticket_id, TICKET_CLOSED_NO_ACTION, actor=req.actor, action_label="reject", comment=req.comment)


@router.post("/chat-tickets/{ticket_id}/request-info", response_model=ChatTicketDetail)
async def request_info_chat_ticket(ticket_id: str, req: TicketDecisionRequest) -> ChatTicketDetail:
    return _decide(ticket_id, TICKET_NEED_MORE_INFO, actor=req.actor, action_label="request_info", comment=req.comment)
