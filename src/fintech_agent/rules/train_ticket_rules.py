"""Train ticket workflow decision rules — deterministic, no LLM.

Decision matrix:
┌─────────────┬──────────────────────┬────────────────────────────────────────┐
│ wallet      │ provider             │ action                                 │
├─────────────┼──────────────────────┼────────────────────────────────────────┤
│ debited     │ ticket_issued+code   │ draft_customer_response (send code)    │
│ debited     │ ticket_not_issued    │ create_refund_request_draft + approval │
│ debited     │ provider_no_record   │ create_reconciliation_ticket_draft     │
│ debited     │ booking_pending      │ wait_sla                               │
│ debited     │ booking_failed       │ create_refund_request_draft + approval │
│ not_debited │ *                    │ draft_customer_response (no charge)    │
│ *           │ conflict detected    │ manual_review                          │
└─────────────┴──────────────────────┴────────────────────────────────────────┘
"""

from __future__ import annotations

from dataclasses import dataclass

from fintech_agent.schemas.enums import ActionType, ProviderStatusValue
from fintech_agent.schemas.evidence import (
    EvidenceBundle,
    RefundStatus,
    TrainProviderStatus,
    WalletLedger,
)
from fintech_agent.rules.refund_rules import full_refund_eligibility_check


@dataclass(frozen=True)
class TrainDecision:
    """Result of train ticket workflow decision."""

    action: ActionType
    diagnosis: str
    approval_required: bool


def decide_train_ticket(
    ledger: WalletLedger | None,
    provider: TrainProviderStatus | None,
    refund: RefundStatus | None,
    evidence: EvidenceBundle,
) -> TrainDecision:
    """Apply train ticket decision matrix.

    Args:
        ledger: Wallet ledger (source of truth for money).
        provider: Train provider status (source of truth for ticket).
        refund: Current refund status.
        evidence: Full evidence bundle (for conflict check).

    Returns:
        TrainDecision with action, diagnosis, and approval flag.
    """
    # Conflicts → always manual review
    if evidence.has_conflicts:
        return TrainDecision(
            action=ActionType.MANUAL_REVIEW,
            diagnosis="conflict_detected",
            approval_required=True,
        )

    # No ledger data → can't proceed
    if ledger is None:
        return TrainDecision(
            action=ActionType.MANUAL_REVIEW,
            diagnosis="wallet_ledger_unavailable",
            approval_required=True,
        )

    # Wallet not debited → no charge, inform customer
    if not ledger.has_user_debit:
        return TrainDecision(
            action=ActionType.DRAFT_CUSTOMER_RESPONSE,
            diagnosis="wallet_not_debited",
            approval_required=False,
        )

    # No provider data → can't determine service delivery
    if provider is None:
        return TrainDecision(
            action=ActionType.MANUAL_REVIEW,
            diagnosis="provider_status_unavailable",
            approval_required=True,
        )

    # Ticket issued with code → send ticket code to customer
    if (
        provider.booking_status == ProviderStatusValue.TICKET_ISSUED
        and provider.ticket_code is not None
    ):
        return TrainDecision(
            action=ActionType.DRAFT_CUSTOMER_RESPONSE,
            diagnosis="ticket_issued_with_code",
            approval_required=False,
        )

    # Provider has no record → need reconciliation
    if provider.booking_status == ProviderStatusValue.PROVIDER_NO_RECORD:
        return TrainDecision(
            action=ActionType.CREATE_RECONCILIATION_TICKET_DRAFT,
            diagnosis="provider_no_record_wallet_debited",
            approval_required=False,
        )

    # Booking pending → wait for SLA
    if provider.booking_status == ProviderStatusValue.BOOKING_PENDING:
        return TrainDecision(
            action=ActionType.WAIT_SLA,
            diagnosis="booking_pending_within_sla",
            approval_required=False,
        )

    # Ticket not issued or booking failed → check refund eligibility
    refund_check = full_refund_eligibility_check(
        ledger=ledger,
        provider_status=provider.booking_status,
        refund=refund,
        evidence=evidence,
    )

    if refund_check.eligible:
        return TrainDecision(
            action=ActionType.CREATE_REFUND_REQUEST_DRAFT,
            diagnosis=f"wallet_debited_ticket_not_issued ({refund_check.reason})",
            approval_required=True,
        )

    # Refund not eligible (e.g., already requested)
    return TrainDecision(
        action=ActionType.NO_ACTION,
        diagnosis=f"refund_not_eligible ({refund_check.reason})",
        approval_required=False,
    )
