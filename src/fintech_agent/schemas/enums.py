"""Domain enums for the fintech workflow agent.

These enums define the controlled vocabulary for the entire system.
No execute_refund action exists by design — agent only creates drafts.
"""

from enum import StrEnum


class ServiceType(StrEnum):
    """Type of fintech service involved in the complaint."""

    TRAIN_TICKET = "train_ticket"
    ELECTRIC_BILL = "electric_bill"
    WATER_BILL = "water_bill"
    WALLET_TOPUP = "wallet_topup"
    ACCOUNT_SECURITY = "account_security"
    UNKNOWN = "unknown"


class IssueType(StrEnum):
    """Category of issue reported by the customer."""

    PAID_BUT_NO_TICKET = "paid_but_no_ticket"
    PAID_BUT_PROVIDER_NOT_CONFIRMED = "paid_but_provider_not_confirmed"
    PROVIDER_FAILED = "provider_failed"
    PROVIDER_NO_RECORD = "provider_no_record"
    DUPLICATE_CHARGE = "duplicate_charge"
    TOPUP_PENDING = "topup_pending"
    ACCOUNT_LOCKED = "account_locked"
    UNKNOWN = "unknown"


class WalletLedgerStatus(StrEnum):
    """Wallet ledger state — source of truth for money in wallet."""

    DEBITED = "debited"
    NOT_DEBITED = "not_debited"
    REFUNDED = "refunded"
    REVERSED = "reversed"
    UNKNOWN = "unknown"


class ProviderStatusValue(StrEnum):
    """Provider-side status — source of truth for service delivery.

    Covers both train ticket and utility bill providers.
    Important: not_confirmed ≠ failed. Do NOT confuse them.
    """

    # Train ticket statuses
    TICKET_ISSUED = "ticket_issued"
    TICKET_NOT_ISSUED = "ticket_not_issued"
    BOOKING_PENDING = "booking_pending"
    BOOKING_FAILED = "booking_failed"

    # Utility bill statuses
    CONFIRMED = "confirmed"
    NOT_CONFIRMED = "not_confirmed"
    PENDING = "pending"
    FAILED = "failed"

    # Shared
    PROVIDER_NO_RECORD = "provider_no_record"
    BILL_CODE_NOT_FOUND = "bill_code_not_found"
    AMOUNT_MISMATCH = "amount_mismatch"
    UNKNOWN = "unknown"


class RefundStatusValue(StrEnum):
    """Refund lifecycle — source of truth for refund state."""

    NOT_REQUESTED = "not_requested"
    REQUESTED = "requested"
    APPROVED = "approved"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"


class CaseStatus(StrEnum):
    """State machine states for a complaint case.

    Transitions must follow the defined state machine — no skipping steps.
    """

    NEW = "new"
    EXTRACTING = "extracting"
    MISSING_INFO = "missing_info"
    FETCHING_EVIDENCE = "fetching_evidence"
    CONFLICT_DETECTED = "conflict_detected"
    ROUTED = "routed"
    RULE_DECISION = "rule_decision"
    RECOMMENDING = "recommending"
    WAITING_APPROVAL = "waiting_approval"
    DRAFT_CREATED = "draft_created"
    MANUAL_REVIEW = "manual_review"
    DEAD_LETTER = "dead_letter"
    CLOSED = "closed"
    REOPENED = "reopened"


class RiskLevel(StrEnum):
    """Risk classification based on amount and action type."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionType(StrEnum):
    """Actions the agent can recommend.

    IMPORTANT: There is NO execute_refund action. Agent only creates drafts.
    This is a safety-by-design decision for the fintech domain.
    """

    DRAFT_CUSTOMER_RESPONSE = "draft_customer_response"
    CREATE_REFUND_REQUEST_DRAFT = "create_refund_request_draft"
    CREATE_RECONCILIATION_TICKET_DRAFT = "create_reconciliation_ticket_draft"
    CREATE_FORCE_SUCCESS_DRAFT = "create_force_success_draft"
    CREATE_UNLOCK_ACCOUNT_DRAFT = "create_unlock_account_draft"
    CREATE_REQUEST_DOCUMENTS_RESPONSE_DRAFT = "create_request_documents_response_draft"
    MANUAL_REVIEW = "manual_review"
    WAIT_SLA = "wait_sla"
    NO_ACTION = "no_action"


class ApprovalStatus(StrEnum):
    """Human approval gate status."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


class AuditEventType(StrEnum):
    """Types of audit events logged throughout the case lifecycle."""

    CASE_RECEIVED = "case_received"
    INFO_EXTRACTED = "info_extracted"
    MISSING_INFO_DETECTED = "missing_info_detected"
    EVIDENCE_FETCH_STARTED = "evidence_fetch_started"
    TOOL_CALLED = "tool_called"
    TOOL_RESULT_RECEIVED = "tool_result_received"
    TOOL_FAILED = "tool_failed"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_RETRY = "tool_retry"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTER_CREATED = "dead_letter_created"
    CONFLICT_DETECTED = "conflict_detected"
    WORKFLOW_ROUTED = "workflow_routed"
    RULE_APPLIED = "rule_applied"
    DIAGNOSIS_GENERATED = "diagnosis_generated"
    ACTION_RECOMMENDED = "action_recommended"
    ACTION_PROPOSED = "action_proposed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_TIMEOUT = "approval_timeout"
    APPROVAL_ESCALATED = "approval_escalated"
    HUMAN_APPROVED = "human_approved"
    HUMAN_REJECTED = "human_rejected"
    HUMAN_EDITED = "human_edited"
    DRAFT_CREATED = "draft_created"
    CASE_CLOSED = "case_closed"
    CASE_REOPENED = "case_reopened"
    LLM_EXTRACTION_FAILED = "llm_extraction_failed"
    SAFETY_BLOCKED = "safety_blocked"
