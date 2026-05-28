"""Utility bill workflow decision rules — deterministic, no LLM.

IMPORTANT: not_confirmed ≠ failed.
  - not_confirmed → reconciliation ticket (provider may still process)
  - failed → refund draft eligible (provider explicitly rejected)

Decision matrix:
┌─────────────┬──────────────────────┬────────────────────────────────────────┐
│ wallet      │ provider             │ action                                 │
├─────────────┼──────────────────────┼────────────────────────────────────────┤
│ debited     │ confirmed/paid       │ draft_customer_response (bill paid)    │
│ debited     │ not_confirmed        │ create_reconciliation_ticket_draft     │
│ debited     │ failed               │ create_refund_request_draft + approval │
│ debited     │ pending              │ wait_sla                               │
│ debited     │ provider_no_record   │ create_reconciliation_ticket_draft     │
│ debited     │ amount_mismatch      │ manual_review                          │
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
    UtilityProviderStatus,
    WalletLedger,
)
from fintech_agent.rules.refund_rules import full_refund_eligibility_check


@dataclass(frozen=True)
class UtilityDecision:
    """Result of utility bill workflow decision."""

    action: ActionType
    diagnosis: str
    approval_required: bool


def decide_utility_bill(
    ledger: WalletLedger | None,
    provider: UtilityProviderStatus | None,
    refund: RefundStatus | None,
    evidence: EvidenceBundle,
) -> UtilityDecision:
    """Apply utility bill decision matrix.

    Args:
        ledger: Wallet ledger (source of truth for money).
        provider: Utility provider status (source of truth for bill payment).
        refund: Current refund status.
        evidence: Full evidence bundle (for conflict check).

    Returns:
        UtilityDecision with action, diagnosis, and approval flag.
    """
    # Conflicts → always manual review
    if evidence.has_conflicts:
        return UtilityDecision(
            action=ActionType.MANUAL_REVIEW,
            diagnosis="conflict_detected",
            approval_required=True,
        )

    # No ledger data → can't proceed
    if ledger is None:
        return UtilityDecision(
            action=ActionType.MANUAL_REVIEW,
            diagnosis="wallet_ledger_unavailable",
            approval_required=True,
        )

    # Wallet not debited → no charge
    if not ledger.has_user_debit:
        return UtilityDecision(
            action=ActionType.DRAFT_CUSTOMER_RESPONSE,
            diagnosis="wallet_not_debited",
            approval_required=False,
        )

    # No provider data → can't determine
    if provider is None:
        return UtilityDecision(
            action=ActionType.MANUAL_REVIEW,
            diagnosis="provider_status_unavailable",
            approval_required=True,
        )

    # Provider confirmed + paid → bill is paid, inform customer
    if provider.provider_status == ProviderStatusValue.CONFIRMED:
        return UtilityDecision(
            action=ActionType.DRAFT_CUSTOMER_RESPONSE,
            diagnosis="bill_confirmed_and_paid",
            approval_required=False,
        )

    # NOT CONFIRMED ≠ FAILED — this is critical
    # not_confirmed → reconciliation ticket (provider may still process)
    if provider.provider_status == ProviderStatusValue.NOT_CONFIRMED:
        return UtilityDecision(
            action=ActionType.CREATE_RECONCILIATION_TICKET_DRAFT,
            diagnosis="provider_not_confirmed_needs_reconciliation",
            approval_required=False,
        )

    # Provider has no record → reconciliation
    if provider.provider_status == ProviderStatusValue.PROVIDER_NO_RECORD:
        return UtilityDecision(
            action=ActionType.CREATE_RECONCILIATION_TICKET_DRAFT,
            diagnosis="provider_no_record_wallet_debited",
            approval_required=False,
        )

    # Pending → wait for SLA
    if provider.provider_status == ProviderStatusValue.PENDING:
        return UtilityDecision(
            action=ActionType.WAIT_SLA,
            diagnosis="provider_pending_within_sla",
            approval_required=False,
        )

    # Amount mismatch → manual review
    if provider.provider_status == ProviderStatusValue.AMOUNT_MISMATCH:
        return UtilityDecision(
            action=ActionType.MANUAL_REVIEW,
            diagnosis="amount_mismatch_between_wallet_and_provider",
            approval_required=True,
        )

    # Failed → check refund eligibility
    if provider.provider_status == ProviderStatusValue.FAILED:
        refund_check = full_refund_eligibility_check(
            ledger=ledger,
            provider_status=provider.provider_status,
            refund=refund,
            evidence=evidence,
        )
        if refund_check.eligible:
            return UtilityDecision(
                action=ActionType.CREATE_REFUND_REQUEST_DRAFT,
                diagnosis=f"provider_failed_wallet_debited ({refund_check.reason})",
                approval_required=True,
            )
        return UtilityDecision(
            action=ActionType.NO_ACTION,
            diagnosis=f"refund_not_eligible ({refund_check.reason})",
            approval_required=False,
        )

    # Unknown/fallback → manual review
    return UtilityDecision(
        action=ActionType.MANUAL_REVIEW,
        diagnosis=f"unknown_provider_status ({provider.provider_status})",
        approval_required=True,
    )
