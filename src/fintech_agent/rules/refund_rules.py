"""Refund eligibility rules — deterministic, no LLM.

A refund request draft can ONLY be created when ALL of these are true:
  1. wallet_ledger.has_user_debit == True
  2. provider indicates failure (ticket_not_issued, failed, etc.)
  3. refund_status == not_requested
  4. No evidence conflicts
  5. Agent NEVER executes refund — only creates draft

This module provides the core check; workflow-specific rules in
train_ticket_rules.py and utility_bill_rules.py call these.
"""

from __future__ import annotations

from dataclasses import dataclass

from fintech_agent.schemas.enums import ProviderStatusValue, RefundStatusValue
from fintech_agent.schemas.evidence import (
    EvidenceBundle,
    RefundStatus,
    WalletLedger,
)


@dataclass(frozen=True)
class RefundEligibility:
    """Result of refund eligibility check."""

    eligible: bool
    reason: str


def check_wallet_debited(ledger: WalletLedger | None) -> RefundEligibility:
    """Rule: cannot refund if wallet was never debited."""
    if ledger is None:
        return RefundEligibility(False, "wallet_ledger not available")
    if not ledger.has_user_debit:
        return RefundEligibility(False, "wallet not debited — no money to refund")
    return RefundEligibility(True, "wallet debited confirmed")


def check_provider_failed(
    provider_status: ProviderStatusValue | None,
) -> RefundEligibility:
    """Rule: cannot refund if provider successfully delivered the service."""
    if provider_status is None:
        return RefundEligibility(False, "provider status not available")

    # Provider statuses that mean "service NOT delivered"
    failed_statuses = {
        ProviderStatusValue.TICKET_NOT_ISSUED,
        ProviderStatusValue.BOOKING_FAILED,
        ProviderStatusValue.FAILED,
    }

    # Provider statuses that mean "service WAS delivered"
    success_statuses = {
        ProviderStatusValue.TICKET_ISSUED,
        ProviderStatusValue.CONFIRMED,
    }

    if provider_status in success_statuses:
        return RefundEligibility(
            False, f"provider delivered service ({provider_status}) — no refund"
        )
    if provider_status in failed_statuses:
        return RefundEligibility(
            True, f"provider failed ({provider_status}) — refund eligible"
        )
    # Ambiguous states (pending, not_confirmed, etc.) — not eligible for refund yet
    return RefundEligibility(
        False, f"provider status ambiguous ({provider_status}) — refund not eligible yet"
    )


def check_no_existing_refund(refund: RefundStatus | None) -> RefundEligibility:
    """Rule: cannot create refund draft if refund already in progress or done."""
    if refund is None:
        return RefundEligibility(True, "no refund record — eligible")

    blocked_statuses = {
        RefundStatusValue.REQUESTED,
        RefundStatusValue.APPROVED,
        RefundStatusValue.EXECUTED,
    }
    if refund.refund_status in blocked_statuses:
        return RefundEligibility(
            False,
            f"refund already {refund.refund_status} — cannot create duplicate",
        )
    if refund.refund_status == RefundStatusValue.REJECTED:
        return RefundEligibility(
            False, "refund was rejected — needs manager review to re-request"
        )
    # not_requested or failed — can proceed
    return RefundEligibility(True, f"refund status is {refund.refund_status} — eligible")


def check_no_conflicts(evidence: EvidenceBundle) -> RefundEligibility:
    """Rule: cannot create refund draft if evidence has conflicts."""
    if evidence.has_conflicts:
        descriptions = [c.description for c in evidence.conflicts]
        return RefundEligibility(
            False,
            f"evidence conflicts detected: {'; '.join(descriptions)}",
        )
    return RefundEligibility(True, "no conflicts — eligible")


def full_refund_eligibility_check(
    ledger: WalletLedger | None,
    provider_status: ProviderStatusValue | None,
    refund: RefundStatus | None,
    evidence: EvidenceBundle,
) -> RefundEligibility:
    """Run ALL refund eligibility checks in order. First failure wins.

    Returns:
        RefundEligibility with eligible=True only if ALL checks pass.
    """
    checks = [
        check_wallet_debited(ledger),
        check_provider_failed(provider_status),
        check_no_existing_refund(refund),
        check_no_conflicts(evidence),
    ]

    for check in checks:
        if not check.eligible:
            return check

    return RefundEligibility(True, "all checks passed — refund draft eligible")
