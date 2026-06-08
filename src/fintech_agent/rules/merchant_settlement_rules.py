"""Merchant settlement delay workflow decision rules — deterministic, no LLM.

Use case: "Đã quá chu kỳ thanh toán D+1 mà tôi chưa nhận được tiền giải ngân."

Decision matrix:
┌─────┬──────────────────────────────────────┬─────────────────────────────────────────────┐
│  #  │ Condition                            │ Action                                      │
├─────┼──────────────────────────────────────┼─────────────────────────────────────────────┤
│  1  │ merchant not found                   │ request_identity_correction                 │
│  2  │ merchant on_hold                     │ manual_settlement_review (escalate Ops)     │
│  3  │ bank account invalid/inactive/fail   │ request_bank_account_correction             │
│  4  │ missing settlement ledger            │ manual_settlement_review                    │
│  5  │ not due yet                          │ draft_customer_response (inform not due)    │
│  6  │ net_settlement_amount <= 0           │ draft_customer_response (send statement)    │
│  7  │ batch failed + verified bank + amt>0 │ create_manual_payout_draft                 │
│  8  │ payout failed (retriable error)      │ create_manual_payout_draft (retry)          │
│  9  │ payout processing/pending            │ draft_customer_response (monitor)           │
│ 10  │ payout success + UNC sent            │ draft_customer_response (reference UNC)     │
│ 11  │ payout success + UNC not sent        │ send_unc_email_draft                        │
│ 12  │ payout amount < ledger net           │ manual_settlement_review (difference)       │
│ 13  │ unknown/conflicting                  │ manual_settlement_review                    │
└─────┴──────────────────────────────────────┴─────────────────────────────────────────────┘

SAFETY:
  - Never payout if bank account is invalid, inactive, or not verified.
  - Never duplicate payout if payout is processing or success.
  - Never use merchant claimed amount — use settlement ledger as source of truth.
  - Manual payout must be draft_only and approval_required.
  - Do not execute payout.
  - Do not send real email.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from fintech_agent.schemas.enums import ActionType
from fintech_agent.schemas.evidence import (
    BankTransferReceipt,
    EvidenceBundle,
    MerchantBankAccount,
    MerchantPayout,
    MerchantProfile,
    MerchantSettlementLedger,
    SettlementBatch,
)


@dataclass(frozen=True)
class MerchantSettlementDecision:
    """Result of merchant settlement delay workflow decision."""

    action: ActionType
    diagnosis: str
    approval_required: bool
    details: dict | None = field(default=None)


def decide_merchant_settlement(
    evidence: EvidenceBundle,
    extracted_info=None,
) -> MerchantSettlementDecision:
    """Apply merchant settlement decision matrix.

    Args:
        evidence: Full evidence bundle (merchant profile, bank account,
                  settlement ledger, payout, batch, receipt).
        extracted_info: Extracted complaint info (not used for decision,
                       only for context).

    Returns:
        MerchantSettlementDecision with action, diagnosis, approval flag.
    """
    merchant = evidence.merchant_profile
    bank_acct = evidence.merchant_bank_account
    ledger = evidence.merchant_settlement_ledger
    payout = evidence.merchant_payout
    batch = evidence.settlement_batch
    receipt = evidence.bank_transfer_receipt

    # ── 1. Merchant not found ─────────────────────────────────
    if merchant is None:
        return MerchantSettlementDecision(
            action=ActionType.REQUEST_IDENTITY_CORRECTION,
            diagnosis="merchant_not_found",
            approval_required=False,
        )

    # ── 2. Merchant on_hold ───────────────────────────────────
    if merchant.status == "on_hold":
        return MerchantSettlementDecision(
            action=ActionType.MANUAL_SETTLEMENT_REVIEW,
            diagnosis="merchant_on_hold_escalate_ops",
            approval_required=True,
        )

    # ── 3. Bank account invalid/inactive/pending ──────────────
    if bank_acct is not None:
        bank_bad = _is_bank_account_invalid(bank_acct)
        if bank_bad:
            return MerchantSettlementDecision(
                action=ActionType.REQUEST_BANK_ACCOUNT_CORRECTION,
                diagnosis=f"bank_account_{bank_bad}",
                approval_required=False,
                details={"reason": bank_bad, "bank_account_id": bank_acct.bank_account_id},
            )
    elif bank_acct is None and merchant is not None:
        # Merchant exists but no bank account on file
        return MerchantSettlementDecision(
            action=ActionType.REQUEST_BANK_ACCOUNT_CORRECTION,
            diagnosis="bank_account_missing",
            approval_required=False,
        )

    # ── 4. Missing settlement ledger ──────────────────────────
    if ledger is None:
        return MerchantSettlementDecision(
            action=ActionType.MANUAL_SETTLEMENT_REVIEW,
            diagnosis="settlement_ledger_not_found",
            approval_required=True,
        )

    # ── 5. Not due yet ────────────────────────────────────────
    if ledger.due_date:
        try:
            due = date.fromisoformat(ledger.due_date)
            if due > date.today():
                return MerchantSettlementDecision(
                    action=ActionType.DRAFT_CUSTOMER_RESPONSE,
                    diagnosis="settlement_not_due_yet",
                    approval_required=False,
                    details={"due_date": ledger.due_date},
                )
        except (ValueError, TypeError):
            pass  # Can't parse date — continue to next rule

    # ── 6. Net settlement amount <= 0 ─────────────────────────
    net_amount = ledger.net_settlement_amount or 0
    if net_amount <= 0:
        return MerchantSettlementDecision(
            action=ActionType.DRAFT_CUSTOMER_RESPONSE,
            diagnosis="net_settlement_zero_or_negative",
            approval_required=False,
            details={
                "gross_amount": ledger.gross_amount,
                "fee_amount": ledger.fee_amount,
                "refund_amount": ledger.refund_amount,
                "chargeback_amount": ledger.chargeback_amount,
                "net_settlement_amount": net_amount,
            },
        )

    # ── From here: net_amount > 0, bank verified, ledger exists ──

    # ── 9. Payout processing/pending → monitor, don't duplicate ──
    if payout is not None and _is_payout_in_progress(payout, receipt):
        return MerchantSettlementDecision(
            action=ActionType.DRAFT_CUSTOMER_RESPONSE,
            diagnosis="payout_in_progress_monitor",
            approval_required=False,
            details={
                "payout_id": payout.payout_id,
                "payout_status": payout.status,
                "duplicate_payout_risk": True,
            },
        )

    # ── 10–12. Payout success branch ────────────────────────────
    if payout is not None and _is_payout_success(payout):
        payout_amount = payout.amount or 0

        # ── 12. Payout amount < ledger net (partial payout) ───
        # Must check BEFORE UNC status — partial payout is a problem
        # even if UNC was already sent.
        if 0 < payout_amount < net_amount:
            return MerchantSettlementDecision(
                action=ActionType.MANUAL_SETTLEMENT_REVIEW,
                diagnosis="payout_amount_less_than_ledger_difference_payout",
                approval_required=True,
                details={
                    "payout_amount": payout_amount,
                    "ledger_net_amount": net_amount,
                    "difference": net_amount - payout_amount,
                },
            )

        # ── 10. Payout success + UNC sent ─────────────────────
        if receipt is not None and receipt.sent_to_merchant is True:
            return MerchantSettlementDecision(
                action=ActionType.DRAFT_CUSTOMER_RESPONSE,
                diagnosis="payout_success_unc_already_sent",
                approval_required=False,
                details={
                    "payout_id": payout.payout_id,
                    "unc_number": receipt.unc_number,
                    "receipt_url": receipt.receipt_url,
                },
            )

        # ── 11. Payout success + UNC not sent ────────────────
        return MerchantSettlementDecision(
            action=ActionType.SEND_UNC_EMAIL_DRAFT,
            diagnosis="payout_success_unc_not_sent",
            approval_required=True,
            details={
                "payout_id": payout.payout_id,
                "unc_number": getattr(receipt, "unc_number", None) if receipt else None,
            },
        )

    # ── 8. Payout failed (retriable error) ────────────────────
    if payout is not None and _is_payout_failed_retriable(payout):
        return MerchantSettlementDecision(
            action=ActionType.CREATE_MANUAL_PAYOUT_DRAFT,
            diagnosis="payout_failed_retriable_retry_payout",
            approval_required=True,
            details={
                "payout_id": payout.payout_id,
                "failure_reason": payout.failure_reason,
                "amount": net_amount,
                "draft_only": True,
            },
        )

    # ── 7. Batch failed/not generated + verified bank + amt > 0 ──
    batch_failed = (
        batch is None
        or (batch.status or "").lower() in ("failed", "not_generated", "error")
    )
    if (payout is None or _is_payout_failed(payout) or _is_payout_not_created(payout)) and batch_failed:
        return MerchantSettlementDecision(
            action=ActionType.CREATE_MANUAL_PAYOUT_DRAFT,
            diagnosis="batch_failed_create_manual_payout",
            approval_required=True,
            details={
                "amount": net_amount,
                "merchant_id": merchant.merchant_id,
                "batch_id": getattr(batch, "batch_id", None),
                "batch_status": getattr(batch, "status", None),
                "draft_only": True,
            },
        )

    # ── Payout failed (non-retriable, e.g. bank rejected) ────
    if payout is not None and _is_payout_failed(payout):
        return MerchantSettlementDecision(
            action=ActionType.MANUAL_SETTLEMENT_REVIEW,
            diagnosis="payout_failed_non_retriable",
            approval_required=True,
            details={
                "payout_id": payout.payout_id,
                "failure_reason": payout.failure_reason,
            },
        )

    # ── 13. Unknown / conflicting evidence ────────────────────
    return MerchantSettlementDecision(
        action=ActionType.MANUAL_SETTLEMENT_REVIEW,
        diagnosis="unknown_or_conflicting_evidence",
        approval_required=True,
    )


# ═══════════════════════════════════════════════════════════════
#  Helper predicates
# ═══════════════════════════════════════════════════════════════


def _is_bank_account_invalid(acct: MerchantBankAccount) -> str | None:
    """Return failure reason string if bank account is unusable, else None."""
    status = (acct.verification_status or "").lower()
    if status in ("pending", "rejected", "failed"):
        return f"verification_{status}"
    if acct.is_active is False:
        return "inactive"
    if acct.failure_reason and status != "verified":
        return f"failure_{acct.failure_reason}"
    if status == "name_mismatch":
        return "name_mismatch"
    return None


def _is_payout_in_progress(payout: MerchantPayout, receipt: BankTransferReceipt | None) -> bool:
    """Check if payout is still in-flight (processing/pending)."""
    pstatus = (payout.status or "").lower()
    if pstatus in ("processing", "pending", "submitted"):
        return True
    # Bank transfer submitted but not confirmed
    if receipt and (receipt.bank_status or "").lower() in ("pending", "processing"):
        return True
    return False


def _is_payout_success(payout: MerchantPayout) -> bool:
    """Check if payout completed successfully."""
    return (payout.status or "").lower() in ("success", "completed", "settled")


def _is_payout_failed(payout: MerchantPayout) -> bool:
    """Check if payout failed."""
    return (payout.status or "").lower() in ("failed", "rejected", "error", "bank_rejected")


def _is_payout_not_created(payout: MerchantPayout | None) -> bool:
    """Check if payout was never created (batch failed before payout generation).

    Status 'not_created' means the settlement batch job failed or was not
    generated before the payout record could be created.  Semantically
    equivalent to payout=None for the batch-failed decision branch.

    SAFETY: This does NOT count as 'failed' for the retriable-error branch
    — it is only used in the batch-failed branch where we already verify
    that the batch itself failed/not_generated.
    """
    if payout is None:
        return False
    return (payout.status or "").lower() == "not_created"


def _is_payout_failed_retriable(payout: MerchantPayout) -> bool:
    """Check if payout failed with a retriable error."""
    if not _is_payout_failed(payout):
        return False
    reason = (payout.failure_reason or "").lower()
    return any(kw in reason for kw in ("timeout", "system_error", "temporary", "retry"))
