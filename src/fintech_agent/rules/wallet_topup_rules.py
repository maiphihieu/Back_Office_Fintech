"""Wallet topup workflow decision rules — deterministic, no LLM.

Use case: "Khách nạp tiền từ ngân hàng vào ví, bank đã trừ tiền nhưng ví vẫn báo 0 đồng."

Decision matrix:
┌─────────────────────┬──────────────┬─────────────────────┬───────────────────────────────────┐
│ transaction.status  │ bank_status  │ money_in_master     │ action                            │
├─────────────────────┼──────────────┼─────────────────────┼───────────────────────────────────┤
│ not pending         │ *            │ *                   │ draft_customer_response            │
│ pending             │ success      │ true                │ create_force_success_draft + ap    │
│ pending             │ success      │ false               │ manual_review                      │
│ pending             │ failed/rej.  │ *                   │ draft_customer_response (cho bank) │
│ pending             │ *            │ *                   │ manual_review (fallback)           │
│ conflict            │ *            │ *                   │ manual_review                      │
└─────────────────────┴──────────────┴─────────────────────┴───────────────────────────────────┘
"""

from __future__ import annotations

from dataclasses import dataclass

from fintech_agent.schemas.enums import ActionType
from fintech_agent.schemas.evidence import (
    EvidenceBundle,
    ReconciliationStatus,
    Transaction,
)


@dataclass(frozen=True)
class WalletTopupDecision:
    """Result of wallet topup workflow decision."""

    action: ActionType
    diagnosis: str
    approval_required: bool


def decide_wallet_topup(
    transaction: Transaction | None,
    reconciliation: ReconciliationStatus | None,
    evidence: EvidenceBundle,
) -> WalletTopupDecision:
    """Apply wallet topup decision matrix.

    Args:
        transaction: Transaction record (source of truth for status).
        reconciliation: Reconciliation record with bank-side data.
        evidence: Full evidence bundle (for conflict check).

    Returns:
        WalletTopupDecision with action, diagnosis, and approval flag.
    """
    # Conflicts -> always manual review
    if evidence.has_conflicts:
        return WalletTopupDecision(
            action=ActionType.MANUAL_REVIEW,
            diagnosis="conflict_detected",
            approval_required=True,
        )

    # No transaction data -> can't proceed
    if transaction is None:
        return WalletTopupDecision(
            action=ActionType.MANUAL_REVIEW,
            diagnosis="transaction_unavailable",
            approval_required=True,
        )

    # Transaction not pending -> already processed or completed
    if transaction.status != "pending":
        return WalletTopupDecision(
            action=ActionType.DRAFT_CUSTOMER_RESPONSE,
            diagnosis=f"transaction_not_pending (status={transaction.status})",
            approval_required=False,
        )

    # No reconciliation data -> can't verify bank side
    if reconciliation is None:
        return WalletTopupDecision(
            action=ActionType.MANUAL_REVIEW,
            diagnosis="reconciliation_unavailable",
            approval_required=True,
        )

    bank_status = (reconciliation.bank_status or "").lower()
    money_received = reconciliation.money_received_in_master_wallet

    # Bank success + money received in master wallet -> force success eligible
    if bank_status == "success" and money_received is True:
        return WalletTopupDecision(
            action=ActionType.CREATE_FORCE_SUCCESS_DRAFT,
            diagnosis="bank_success_money_received_wallet_pending",
            approval_required=True,
        )

    # Bank success but money NOT received -> needs investigation
    if bank_status == "success" and money_received is False:
        return WalletTopupDecision(
            action=ActionType.MANUAL_REVIEW,
            diagnosis="bank_success_but_money_not_in_master_wallet",
            approval_required=True,
        )

    # Bank failed/rejected -> inform customer to wait for bank reversal
    if bank_status in ("failed", "fail", "rejected"):
        return WalletTopupDecision(
            action=ActionType.DRAFT_CUSTOMER_RESPONSE,
            diagnosis="bank_transfer_failed_wait_reversal",
            approval_required=False,
        )

    # Money not received regardless of bank status -> customer response
    if money_received is False:
        return WalletTopupDecision(
            action=ActionType.DRAFT_CUSTOMER_RESPONSE,
            diagnosis="money_not_received_in_master_wallet",
            approval_required=False,
        )

    # Fallback -> manual review
    return WalletTopupDecision(
        action=ActionType.MANUAL_REVIEW,
        diagnosis=f"unknown_bank_status ({bank_status})",
        approval_required=True,
    )
