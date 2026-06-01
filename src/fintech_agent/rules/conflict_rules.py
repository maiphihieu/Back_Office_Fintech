"""Conflict detection rules — cross-source inconsistency checks.

When ANY conflict is detected, the agent MUST NOT diagnose or recommend.
Route to manual_review instead.
"""

from __future__ import annotations

from fintech_agent.schemas.evidence import (
    EvidenceBundle,
    EvidenceConflict,
    RefundStatus,
    TrainProviderStatus,
    Transaction,
    WalletLedger,
)
from fintech_agent.schemas.enums import ProviderStatusValue, RefundStatusValue


def detect_all_conflicts(
    evidence: EvidenceBundle,
    case_user_id: str | None = None,
) -> list[EvidenceConflict]:
    """Run all system-vs-system conflict checks.

    NOTE: Customer-vs-system mismatches (e.g. claimed amount != system amount)
    are NO LONGER detected here. They are handled by claim_verifier.py which
    produces non-blocking ClaimVerification results.

    This function only detects conflicts between TRUSTED system sources,
    which are blocking and route to manual_review.

    Args:
        evidence: The collected evidence bundle.
        case_user_id: The user_id from the case (for ownership check).

    Returns:
        List of EvidenceConflict objects. Empty list = no conflicts.
    """
    conflicts: list[EvidenceConflict] = []

    if evidence.wallet_ledger and evidence.transaction:
        conflicts.extend(
            _check_ledger_vs_transaction(evidence.wallet_ledger, evidence.transaction)
        )

    if evidence.train_provider:
        conflicts.extend(
            _check_provider_success_but_no_ticket(evidence.train_provider)
        )

    if evidence.wallet_ledger and evidence.refund_status:
        conflicts.extend(
            _check_refund_vs_ledger(evidence.refund_status, evidence.wallet_ledger)
        )

    if evidence.transaction and case_user_id:
        conflicts.extend(
            _check_user_ownership(evidence.transaction, case_user_id)
        )

    return conflicts


def _check_ledger_vs_transaction(
    ledger: WalletLedger, txn: Transaction
) -> list[EvidenceConflict]:
    """Conflict: wallet ledger debited but transaction.status is pending.

    This means money left the wallet but the transaction system hasn't
    confirmed it yet — data is inconsistent.
    """
    if ledger.has_user_debit and txn.status == "pending":
        return [
            EvidenceConflict(
                source_a="wallet_ledger",
                source_b="transaction",
                field="status",
                value_a=f"debited (amount={ledger.debit_amount})",
                value_b=f"status={txn.status}",
                description=(
                    "Wallet ledger shows debit but transaction status is still "
                    "pending. Data inconsistency — route to manual review."
                ),
            )
        ]
    return []


def _check_provider_success_but_no_ticket(
    provider: TrainProviderStatus,
) -> list[EvidenceConflict]:
    """Conflict: provider says success/ticket_issued but ticket_code is null.

    Provider claims ticket was issued but cannot provide a ticket code.
    """
    success_statuses = {
        ProviderStatusValue.TICKET_ISSUED,
        ProviderStatusValue.CONFIRMED,
    }
    if provider.booking_status in success_statuses and provider.ticket_code is None:
        return [
            EvidenceConflict(
                source_a="provider_status",
                source_b="provider_status",
                field="ticket_code",
                value_a=f"booking_status={provider.booking_status}",
                value_b="ticket_code=null",
                description=(
                    "Provider reports success but ticket_code is null. "
                    "Cannot verify service delivery — route to manual review."
                ),
            )
        ]
    return []


def _check_refund_vs_ledger(
    refund: RefundStatus, ledger: WalletLedger
) -> list[EvidenceConflict]:
    """Conflict: refund executed but wallet ledger has no refund credit.

    If refund was executed, the wallet should show a credit entry.
    """
    if (
        refund.refund_status == RefundStatusValue.EXECUTED
        and not ledger.has_credit_refund
    ):
        return [
            EvidenceConflict(
                source_a="refund_table",
                source_b="wallet_ledger",
                field="refund_credit",
                value_a=f"refund_status={refund.refund_status}",
                value_b="has_credit_refund=false",
                description=(
                    "Refund table shows executed but wallet ledger has no "
                    "refund credit. Money may not have been returned — "
                    "route to manual review."
                ),
            )
        ]
    return []


def _check_user_ownership(
    txn: Transaction, case_user_id: str
) -> list[EvidenceConflict]:
    """Conflict: transaction belongs to a different user than the case.

    This could indicate fraud or a data entry error.
    """
    if txn.user_id != case_user_id:
        return [
            EvidenceConflict(
                source_a="transaction",
                source_b="case",
                field="user_id",
                value_a=f"transaction.user_id={txn.user_id}",
                value_b=f"case.user_id={case_user_id}",
                description=(
                    "Transaction belongs to a different user than the "
                    "complainant. Possible fraud or data error — "
                    "route to manual review."
                ),
            )
        ]
    return []


# NOTE: _check_amount_mismatch has been removed.
# Customer-vs-system amount mismatches are now handled by
# fintech_agent.rules.claim_verifier.verify_all_claims()
# which produces non-blocking ClaimVerification results.
# Only system-vs-system conflicts remain in this module.
