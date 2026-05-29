"""Draft action tools — CREATE drafts only, NEVER execute financial ops.

Every draft tool:
  1. Calls money_action_guard first (blocks forbidden actions)
  2. Checks idempotency (prevents duplicates)
  3. Returns a structured result with the created draft
  4. Stores drafts in-memory for the session

There is NO execute_refund tool. There is NO update_wallet_balance tool.
There is NO edit_ledger tool. This is by design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from fintech_agent.rules.idempotency_rules import (
    generate_idempotency_key,
    is_duplicate_action,
)
from fintech_agent.safety.money_action_guard import guard_action
from fintech_agent.schemas.actions import (
    RecommendedAction,
    ReconciliationTicketDraft,
    RefundRequestDraft,
)
from fintech_agent.schemas.enums import ActionType, RefundStatusValue
from fintech_agent.schemas.evidence import RefundStatus
from fintech_agent.tools.tool_errors import (
    DuplicateActionError,
    ToolValidationError,
)


# ─── In-memory draft store ──────────────────────────────────


class DraftStore:
    """In-memory store for draft actions (session-scoped).

    For MVP, drafts are lost on restart.
    Can be swapped to persistent store later.
    """

    def __init__(self) -> None:
        self._refund_drafts: dict[str, RefundRequestDraft] = {}
        self._reconciliation_drafts: dict[str, ReconciliationTicketDraft] = {}
        self._response_drafts: dict[str, CustomerResponseDraft] = {}
        self._force_success_drafts: dict[str, "ForceSuccessDraft"] = {}

    def has_refund_draft(self, idempotency_key: str) -> bool:
        return idempotency_key in self._refund_drafts

    def save_refund_draft(self, draft: RefundRequestDraft) -> None:
        self._refund_drafts[draft.idempotency_key] = draft

    def save_reconciliation_draft(self, draft: ReconciliationTicketDraft) -> None:
        self._reconciliation_drafts[draft.idempotency_key] = draft

    def save_response_draft(self, draft: "CustomerResponseDraft") -> None:
        self._response_drafts[draft.case_id] = draft

    def get_refund_draft(self, idempotency_key: str) -> RefundRequestDraft | None:
        return self._refund_drafts.get(idempotency_key)

    def get_reconciliation_draft(self, key: str) -> ReconciliationTicketDraft | None:
        return self._reconciliation_drafts.get(key)

    def has_force_success_draft(self, idempotency_key: str) -> bool:
        return idempotency_key in self._force_success_drafts

    def save_force_success_draft(self, draft: "ForceSuccessDraft") -> None:
        self._force_success_drafts[draft.idempotency_key] = draft


# Singleton store for the session
_default_store = DraftStore()


def get_default_store() -> DraftStore:
    """Get the default draft store (module-level singleton)."""
    return _default_store


def reset_default_store() -> None:
    """Reset the default store (for testing)."""
    global _default_store
    _default_store = DraftStore()


# ─── Result types ───────────────────────────────────────────


@dataclass(frozen=True)
class RefundDraftResult:
    """Structured result from create_refund_request_draft."""

    success: bool
    draft: RefundRequestDraft | None = None
    idempotency_key: str = ""
    error: str | None = None


@dataclass(frozen=True)
class ReconciliationDraftResult:
    """Structured result from create_reconciliation_ticket_draft."""

    success: bool
    draft: ReconciliationTicketDraft | None = None
    idempotency_key: str = ""
    error: str | None = None


@dataclass(frozen=True)
class CustomerResponseDraft:
    """A draft customer response (simple text)."""

    case_id: str
    transaction_id: str
    message: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class CustomerResponseResult:
    """Structured result from create_customer_response_draft."""

    success: bool
    draft: CustomerResponseDraft | None = None
    error: str | None = None


# ─── Draft tools ────────────────────────────────────────────


def create_refund_request_draft(
    case_id: str,
    transaction_id: str,
    user_id: str,
    amount: int,
    reason: str,
    evidence_summary: list[str],
    refund_status: RefundStatus | None = None,
    store: DraftStore | None = None,
) -> RefundDraftResult:
    """Create a refund request draft.

    Safety checks:
      1. guard_action blocks forbidden actions
      2. idempotency check prevents duplicates
      3. existing refund status check

    Args:
        case_id: The case this refund belongs to.
        transaction_id: The transaction to refund.
        user_id: The user who owns the transaction.
        amount: Refund amount (from wallet_ledger.debit_amount).
        reason: Human-readable reason.
        evidence_summary: List of evidence items supporting the refund.
        refund_status: Current refund status (for duplicate check).
        store: Optional DraftStore override.

    Returns:
        RefundDraftResult.

    Raises:
        SafetyViolation: If a forbidden action is attempted.
        DuplicateActionError: If a duplicate draft is detected.
        ToolValidationError: If input validation fails.
    """
    # 1. Safety guard — will raise SafetyViolation if forbidden
    guard_action("create_refund_request_draft", context=f"{case_id}:{transaction_id}")

    # 2. Input validation
    if amount <= 0:
        raise ToolValidationError(
            "create_refund_request_draft", "amount", "must be positive"
        )
    if not evidence_summary or all(not s.strip() for s in evidence_summary):
        raise ToolValidationError(
            "create_refund_request_draft",
            "evidence_summary",
            "must not be empty",
        )

    # 3. Idempotency key
    idem_key = generate_idempotency_key(
        transaction_id, ActionType.CREATE_REFUND_REQUEST_DRAFT, amount
    )

    # 4. Check for duplicate in store
    _store = store or get_default_store()
    if _store.has_refund_draft(idem_key):
        raise DuplicateActionError("create_refund_request_draft", idem_key)

    # 5. Check existing refund status
    if is_duplicate_action(
        transaction_id, ActionType.CREATE_REFUND_REQUEST_DRAFT, amount, refund_status
    ):
        raise DuplicateActionError("create_refund_request_draft", idem_key)

    # 6. Create draft
    draft = RefundRequestDraft(
        case_id=case_id,
        transaction_id=transaction_id,
        user_id=user_id,
        amount=amount,
        reason=reason,
        evidence_summary=evidence_summary,
        idempotency_key=idem_key,
    )

    # 7. Save to store
    _store.save_refund_draft(draft)

    return RefundDraftResult(
        success=True,
        draft=draft,
        idempotency_key=idem_key,
    )


def create_reconciliation_ticket_draft(
    case_id: str,
    transaction_id: str,
    user_id: str,
    mismatch_type: str,
    evidence_summary: list[str],
    provider_ref_id: str | None = None,
    store: DraftStore | None = None,
) -> ReconciliationDraftResult:
    """Create a reconciliation ticket draft.

    Args:
        case_id: The case this ticket belongs to.
        transaction_id: The mismatched transaction.
        user_id: The user who owns the transaction.
        mismatch_type: Type of mismatch detected.
        evidence_summary: List of evidence items.
        provider_ref_id: Optional provider reference ID.
        store: Optional DraftStore override.

    Returns:
        ReconciliationDraftResult.
    """
    # Safety guard
    guard_action(
        "create_reconciliation_ticket_draft",
        context=f"{case_id}:{transaction_id}",
    )

    # Validation
    if not evidence_summary or all(not s.strip() for s in evidence_summary):
        raise ToolValidationError(
            "create_reconciliation_ticket_draft",
            "evidence_summary",
            "must not be empty",
        )

    # Idempotency key
    idem_key = generate_idempotency_key(
        transaction_id, ActionType.CREATE_RECONCILIATION_TICKET_DRAFT, 0
    )

    _store = store or get_default_store()

    draft = ReconciliationTicketDraft(
        case_id=case_id,
        transaction_id=transaction_id,
        user_id=user_id,
        mismatch_type=mismatch_type,
        evidence_summary=evidence_summary,
        provider_ref_id=provider_ref_id,
        idempotency_key=idem_key,
    )

    _store.save_reconciliation_draft(draft)

    return ReconciliationDraftResult(
        success=True,
        draft=draft,
        idempotency_key=idem_key,
    )


def create_customer_response_draft(
    case_id: str,
    transaction_id: str,
    message: str,
    store: DraftStore | None = None,
) -> CustomerResponseResult:
    """Create a customer response draft.

    This is a low-risk action — no approval required.

    Args:
        case_id: The case this response belongs to.
        transaction_id: The related transaction.
        message: The response message.
        store: Optional DraftStore override.

    Returns:
        CustomerResponseResult.
    """
    # Safety guard (should pass — this is an allowed action)
    guard_action(
        "create_customer_response_draft",
        context=f"{case_id}:{transaction_id}",
    )

    if not message.strip():
        raise ToolValidationError(
            "create_customer_response_draft", "message", "must not be empty"
        )

    draft = CustomerResponseDraft(
        case_id=case_id,
        transaction_id=transaction_id,
        message=message,
    )

    _store = store or get_default_store()
    _store.save_response_draft(draft)

    return CustomerResponseResult(success=True, draft=draft)


# ─── Force Success Draft ────────────────────────────────────


@dataclass(frozen=True)
class ForceSuccessDraft:
    """A draft to force-success a pending transaction.

    This does NOT execute any financial operation.
    It only creates a draft that requires human approval.
    After approval, operations team manually updates the ledger.
    """

    case_id: str
    transaction_id: str
    user_id: str
    amount: int
    reason: str
    evidence_summary: list[str]
    idempotency_key: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ForceSuccessDraftResult:
    """Structured result from create_force_success_draft."""

    success: bool
    draft: ForceSuccessDraft | None = None
    idempotency_key: str = ""
    error: str | None = None


def create_force_success_draft(
    case_id: str,
    transaction_id: str,
    user_id: str,
    amount: int,
    reason: str,
    evidence_summary: list[str],
    store: DraftStore | None = None,
) -> ForceSuccessDraftResult:
    """Create a force-success draft for a pending wallet topup.

    This is a HIGH-RISK action that requires human approval.
    It does NOT modify any ledger or wallet balance.

    Safety checks:
      1. guard_action blocks forbidden actions
      2. idempotency check prevents duplicates

    Args:
        case_id: The case this draft belongs to.
        transaction_id: The stuck pending transaction.
        user_id: The user who owns the transaction.
        amount: The topup amount.
        reason: Human-readable reason from rule engine.
        evidence_summary: List of evidence items.
        store: Optional DraftStore override.

    Returns:
        ForceSuccessDraftResult.

    Raises:
        SafetyViolation: If a forbidden action is attempted.
        DuplicateActionError: If a duplicate draft is detected.
        ToolValidationError: If input validation fails.
    """
    # 1. Safety guard — will raise SafetyViolation if forbidden
    guard_action("create_force_success_draft", context=f"{case_id}:{transaction_id}")

    # 2. Input validation
    if amount <= 0:
        raise ToolValidationError(
            "create_force_success_draft", "amount", "must be positive"
        )
    if not evidence_summary or all(not s.strip() for s in evidence_summary):
        raise ToolValidationError(
            "create_force_success_draft",
            "evidence_summary",
            "must not be empty",
        )

    # 3. Idempotency key
    idem_key = generate_idempotency_key(
        transaction_id, ActionType.CREATE_FORCE_SUCCESS_DRAFT, amount
    )

    # 4. Check for duplicate in store
    _store = store or get_default_store()
    if _store.has_force_success_draft(idem_key):
        raise DuplicateActionError("create_force_success_draft", idem_key)

    # 5. Create draft
    draft = ForceSuccessDraft(
        case_id=case_id,
        transaction_id=transaction_id,
        user_id=user_id,
        amount=amount,
        reason=reason,
        evidence_summary=evidence_summary,
        idempotency_key=idem_key,
    )

    # 6. Save to store
    _store.save_force_success_draft(draft)

    return ForceSuccessDraftResult(
        success=True,
        draft=draft,
        idempotency_key=idem_key,
    )
