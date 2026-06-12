"""Schemas for customer-chat → back-office handoff tickets.

A ChatHandoffTicket is the ONE back-office record created/updated when a
customer chat ends, expires, or the customer requests staff support.

SECURITY:
  - Complainant identity (user_id/wallet_id/merchant_id/tax_code) is for
    BACK-OFFICE STAFF only — never returned to the customer chat frontend.
  - PIN/OTP/password/full card are NEVER stored (redact before building).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Back-office ticket lifecycle statuses
TICKET_PENDING_REVIEW = "pending_review"
TICKET_PENDING_APPROVAL = "pending_approval"
TICKET_NEED_MORE_INFO = "need_more_info"
TICKET_CLOSED_NO_ACTION = "closed_no_action"

VALID_TICKET_STATUSES = frozenset({
    TICKET_PENDING_REVIEW,
    TICKET_PENDING_APPROVAL,
    TICKET_NEED_MORE_INFO,
    TICKET_CLOSED_NO_ACTION,
})

# Handoff source (vs staff-created cases)
SOURCE_CUSTOMER_CHAT = "customer_chat"
SOURCE_STAFF = "staff"


@dataclass
class ComplainantInfo:
    """Trusted complainant identity, sourced from the server-side session.

    BACK-OFFICE ONLY. Never serialized to the customer chat frontend.
    """
    subject_type: str = ""                 # wallet_user | merchant
    display_name: str = ""
    phone: str = ""
    email: str = ""
    # wallet_user
    user_id: str = ""
    wallet_id: str = ""
    account_status: str = ""
    wallet_status: str = ""
    # merchant
    merchant_name: str = ""
    merchant_id: str = ""
    tax_code: str = ""
    bank_account_status: str = ""
    settlement_cycle: str = ""


@dataclass
class ChatMessageRecord:
    """One redacted message in the conversation timeline."""
    role: str = ""        # customer | agent
    text: str = ""        # already redacted
    timestamp: str = ""


@dataclass
class ChatHandoffTicket:
    """A back-office ticket created from a customer-chat handoff."""

    ticket_id: str = ""
    source: str = SOURCE_CUSTOMER_CHAT

    # Link to the customer chat case (dedup key) + public reference
    customer_chat_case_id: str = ""
    customer_session_id: str = ""
    public_case_ref: str = ""

    subject_type: str = ""

    # ── Complainant (staff-only) ──
    complainant: ComplainantInfo = field(default_factory=ComplainantInfo)

    # ── Conversation summary (redacted) ──
    conversation_summary: str = ""
    customer_problem: str = ""
    customer_emotion: str = "neutral"
    key_customer_claims: list[str] = field(default_factory=list)
    customer_provided_info: list[str] = field(default_factory=list)
    latest_customer_message: str = ""
    timeline: list[ChatMessageRecord] = field(default_factory=list)

    # ── Agent diagnosis (public-safe) ──
    selected_workflow: str = ""
    issue_type: str = ""
    public_safe_diagnosis: dict = field(default_factory=dict)
    diagnosis_confidence: str = "low"

    # ── Staff evidence summary (no raw sensitive values) ──
    internal_staff_evidence_summary: dict = field(default_factory=dict)

    # ── Extracted info from customer message (actual values, never placeholder labels) ──
    extracted_info: dict = field(default_factory=dict)

    # ── Recommended action ──
    recommended_action: str = ""
    approval_required: bool = False
    risk_level: str = "unknown"
    linked_action_draft_id: str = ""

    # ── Back-office workflow ──
    backoffice_ticket_status: str = TICKET_PENDING_REVIEW
    assigned_team: str = ""
    handoff_reason: str = ""          # chat_active | ended | expired | staff_request

    # ── Audit log (in-memory, per-ticket) ──
    audit_log: list[dict] = field(default_factory=list)

    # ── Customer Claims vs Verified Evidence (data-driven) ──
    customer_claims_data: list[dict] = field(default_factory=list)
    verified_evidence_data: list[dict] = field(default_factory=list)
    contradictions_data: list[dict] = field(default_factory=list)

    created_at: str = field(default_factory=lambda: _now().isoformat())
    updated_at: str = field(default_factory=lambda: _now().isoformat())

    def touch(self) -> None:
        self.updated_at = _now().isoformat()
