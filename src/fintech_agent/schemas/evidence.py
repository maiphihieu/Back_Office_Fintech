"""Evidence models — data retrieved from source-of-truth systems.

Source of truth hierarchy (highest → lowest):
  1. wallet_ledger   — money in wallet
  2. refund_table    — refund state
  3. provider_status — service delivery
  4. transaction     — metadata (can lag)
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from fintech_agent.schemas.enums import (
    ProviderStatusValue,
    RefundStatusValue,
    WalletLedgerStatus,
)


# ─── Transaction ────────────────────────────────────────────


class Transaction(BaseModel):
    """Transaction record — metadata about the payment.

    This is NOT the source of truth for money; wallet_ledger is.
    Transaction status can lag behind actual ledger state.
    """

    transaction_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    service_type: str
    amount: int = Field(..., ge=0, description="Amount in VND, must be >= 0")
    status: str
    order_id: str | None = None
    bill_code: str | None = None
    customer_code: str | None = None
    provider_ref_id: str | None = None
    created_at: datetime | None = None


# ─── Wallet Ledger ──────────────────────────────────────────


class WalletLedgerEntry(BaseModel):
    """Single ledger entry (debit or credit)."""

    entry_type: str = Field(..., description="debit | credit")
    amount: int = Field(..., ge=0)
    balance_after: int | None = None
    reason: str | None = None
    created_at: datetime | None = None


class WalletLedger(BaseModel):
    """Wallet ledger — source of truth for money in wallet.

    Agent must use debit_amount from here for refund, never from complaint text.
    """

    transaction_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    entries: list[WalletLedgerEntry] = Field(default_factory=list)
    status: WalletLedgerStatus = WalletLedgerStatus.UNKNOWN
    has_user_debit: bool = False
    debit_amount: int = Field(default=0, ge=0)
    has_credit_refund: bool = False
    credit_refund_amount: int = Field(default=0, ge=0)
    net_amount: int = 0


# ─── Provider Status ───────────────────────────────────────


class TrainProviderStatus(BaseModel):
    """Train ticket provider status — source of truth for ticket delivery."""

    provider_ref_id: str = Field(..., min_length=1)
    booking_status: ProviderStatusValue = ProviderStatusValue.UNKNOWN
    ticket_code: str | None = None
    departure: datetime | None = None


class UtilityProviderStatus(BaseModel):
    """Utility bill provider status — source of truth for bill payment.

    Important: not_confirmed ≠ failed.
    not_confirmed → may need reconciliation ticket.
    failed → may qualify for refund draft.
    """

    provider_ref_id: str = Field(..., min_length=1)
    provider_status: ProviderStatusValue = ProviderStatusValue.UNKNOWN
    bill_status: str | None = None
    bill_code: str | None = None
    customer_code: str | None = None
    amount: int | None = Field(default=None, ge=0)


# ─── Refund ─────────────────────────────────────────────────


class RefundStatus(BaseModel):
    """Refund record — source of truth for refund state."""

    transaction_id: str = Field(..., min_length=1)
    refund_status: RefundStatusValue = RefundStatusValue.NOT_REQUESTED
    refund_amount: int | None = Field(default=None, ge=0)
    refund_id: str | None = None
    requested_at: datetime | None = None
    executed_at: datetime | None = None


# ─── Reconciliation ────────────────────────────────────────


class ReconciliationStatus(BaseModel):
    """Reconciliation record — tracks wallet↔provider mismatch.

    Extended for wallet_topup use case: bank-side reconciliation fields
    are stored in the `details` jsonb column in Supabase.
    """

    transaction_id: str = Field(..., min_length=1)
    status: str | None = None
    mismatch_type: str | None = None
    ticket_id: str | None = None
    created_at: datetime | None = None
    # Bank reconciliation fields (wallet_topup)
    bank_status: str | None = None
    bank_amount: int | None = None
    money_received_in_master_wallet: bool | None = None
    bank_ref_id: str | None = None
    note: str | None = None


# ─── Account Status (fraud/lock) ────────────────────────────


class AccountStatus(BaseModel):
    """Account status — source of truth for account lock state.

    Used in use case 2: Account locked by Fraud Detection.
    """

    user_id: str = Field(..., min_length=1)
    wallet_id: str | None = None
    account_status: str | None = None
    withdrawal_enabled: bool | None = None
    lock_reason: str | None = None
    current_balance: int | float | None = None
    locked_at: datetime | None = None


class FraudCase(BaseModel):
    """Fraud case record — evidence from fraud detection system.

    Contains risk scoring, signals, and recommended decision.
    """

    fraud_case_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    risk_score: int | None = None
    risk_level: str | None = None
    fraud_status: str | None = None
    trigger_reason: str | None = None
    signals: dict = Field(default_factory=dict)
    recent_transactions: list[dict] = Field(default_factory=list)
    device_events: list[dict] = Field(default_factory=list)
    recommended_decision: str | None = None


# ─── Evidence Conflict ──────────────────────────────────────


class EvidenceConflict(BaseModel):
    """A detected conflict between two sources of truth.

    When conflicts exist, agent must NOT diagnose or recommend.
    Route to manual_review instead.
    """

    source_a: str
    source_b: str
    field: str
    value_a: str
    value_b: str
    description: str
    conflict_type: str | None = Field(
        default=None,
        description="Loại conflict: 'data_inconsistency' | 'amount_mismatch' | 'ownership' | etc.",
    )
    severity: str = Field(
        default="high",
        description="'low' | 'medium' | 'high'",
    )


# ─── Merchant Settlement ────────────────────────────────────


class MerchantProfile(BaseModel):
    """Merchant profile — identity and settlement config."""

    merchant_id: str = Field(..., min_length=1)
    merchant_name: str | None = None
    tax_code: str | None = None
    contact_email: str | None = None
    phone: str | None = None
    status: str | None = None
    settlement_cycle: str | None = None
    bank_account_id: str | None = None
    created_at: datetime | None = None


class MerchantBankAccount(BaseModel):
    """Merchant bank account — destination for settlement payouts."""

    bank_account_id: str = Field(..., min_length=1)
    merchant_id: str = Field(..., min_length=1)
    bank_code: str | None = None
    bank_name: str | None = None
    account_number: str | None = None
    account_holder_name: str | None = None
    branch_name: str | None = None
    verification_status: str | None = None
    is_active: bool | None = None
    failure_reason: str | None = None
    last_verified_at: datetime | None = None
    updated_at: datetime | None = None


class SettlementBatch(BaseModel):
    """Settlement batch — batch-level settlement processing record."""

    batch_id: str = Field(..., min_length=1)
    settlement_date: str | None = None
    cycle: str | None = None
    status: str | None = None
    total_merchants: int | None = None
    total_amount: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_reason: str | None = None
    created_at: datetime | None = None


class MerchantSettlementLedger(BaseModel):
    """Merchant settlement ledger — per-merchant settlement breakdown."""

    ledger_id: str = Field(..., min_length=1)
    merchant_id: str = Field(..., min_length=1)
    settlement_date: str | None = None
    due_date: str | None = None
    gross_amount: int | None = None
    fee_amount: int | None = None
    refund_amount: int | None = None
    chargeback_amount: int | None = None
    net_settlement_amount: int | None = None
    currency: str | None = None
    status: str | None = None
    created_at: datetime | None = None


class MerchantPayout(BaseModel):
    """Merchant payout — individual disbursement attempt."""

    payout_id: str = Field(..., min_length=1)
    batch_id: str | None = None
    merchant_id: str = Field(..., min_length=1)
    settlement_date: str | None = None
    bank_account_id: str | None = None
    amount: int | None = None
    currency: str | None = None
    status: str | None = None
    bank_transfer_ref: str | None = None
    failure_reason: str | None = None
    scheduled_date: str | None = None
    executed_at: datetime | None = None
    created_at: datetime | None = None


class BankTransferReceipt(BaseModel):
    """Bank transfer receipt — UNC confirmation from bank."""

    receipt_id: str = Field(..., min_length=1)
    payout_id: str | None = None
    bank_transfer_ref: str | None = None
    bank_status: str | None = None
    unc_number: str | None = None
    receipt_url: str | None = None
    sent_to_merchant: bool | None = None
    sent_at: datetime | None = None
    created_at: datetime | None = None



class EvidenceBundle(BaseModel):
    """All evidence collected for a case.

    The agent gathers this by calling read-only tools.
    """

    transaction: Transaction | None = None
    wallet_ledger: WalletLedger | None = None
    train_provider: TrainProviderStatus | None = None
    utility_provider: UtilityProviderStatus | None = None
    refund_status: RefundStatus | None = None
    reconciliation_status: ReconciliationStatus | None = None
    account_status: AccountStatus | None = None
    fraud_case: FraudCase | None = None
    # Merchant settlement evidence
    merchant_profile: MerchantProfile | None = None
    merchant_bank_account: MerchantBankAccount | None = None
    settlement_batch: SettlementBatch | None = None
    merchant_settlement_ledger: MerchantSettlementLedger | None = None
    merchant_payout: MerchantPayout | None = None
    bank_transfer_receipt: BankTransferReceipt | None = None
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    tool_errors: list[str] = Field(
        default_factory=list,
        description="Tools that failed after retries",
    )

    @property
    def has_conflicts(self) -> bool:
        """Return True if any data conflicts were detected."""
        return len(self.conflicts) > 0

    @property
    def has_critical_failures(self) -> bool:
        """Return True if critical tools (wallet_ledger, transaction) failed."""
        critical = {"get_wallet_ledger", "get_transaction"}
        return bool(critical & set(self.tool_errors))

