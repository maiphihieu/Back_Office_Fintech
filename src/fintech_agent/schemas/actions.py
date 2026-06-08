"""Action drafts — outputs the agent produces.

IMPORTANT: Agent only creates DRAFTS. It never executes refunds or modifies ledgers.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from fintech_agent.schemas.enums import ActionType, RiskLevel


class RecommendedAction(BaseModel):
    """Agent's recommendation for the case.

    The action_type comes from the rule engine, not from the LLM.
    LLM only generates the human-readable summary.
    """

    action_type: ActionType
    diagnosis: str
    summary: str
    risk_level: RiskLevel
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="Keys of evidence used to reach this conclusion",
    )
    approval_required: bool = False


class RefundRequestDraft(BaseModel):
    """Draft refund request — requires human approval before execution.

    amount MUST come from wallet_ledger.debit_amount, never from complaint text.
    """

    idempotency_key: str = Field(
        ...,
        min_length=1,
        description="hash(transaction_id:action_type:amount) to prevent duplicates",
    )
    case_id: str = Field(..., min_length=1)
    transaction_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    amount: int = Field(
        ...,
        ge=0,
        description="Amount in VND from wallet_ledger, NOT from complaint",
    )
    reason: str
    evidence_summary: list[str] = Field(
        ...,
        min_length=1,
        description="Must not be empty — reviewer needs evidence",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReconciliationTicketDraft(BaseModel):
    """Draft reconciliation ticket for wallet↔provider mismatch."""

    idempotency_key: str = Field(
        ...,
        min_length=1,
        description="hash(transaction_id:action_type:amount) to prevent duplicates",
    )
    case_id: str = Field(..., min_length=1)
    transaction_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    mismatch_type: str
    provider_ref_id: str | None = None
    evidence_summary: list[str] = Field(
        ...,
        min_length=1,
        description="Must not be empty — reviewer needs evidence",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ═══════════════════════════════════════════════════════════════
#  Merchant settlement draft schemas (Case 3)
#  All are DRAFT-ONLY — no real payout, no real email, no bank update.
# ═══════════════════════════════════════════════════════════════


class ManualPayoutDraft(BaseModel):
    """Draft manual payout request — requires human approval before execution.

    Amount MUST come from settlement ledger net_settlement_amount,
    never from merchant complaint text.
    """

    case_id: str = Field(..., min_length=1)
    merchant_id: str = Field(..., min_length=1)
    settlement_date: str | None = None
    amount: int = Field(
        ...,
        ge=0,
        description="Amount in VND from settlement_ledger.net_settlement_amount, NOT from complaint",
    )
    currency: str = Field(default="VND")
    bank_account_id: str | None = None
    reason: str = Field(..., min_length=1)
    trusted_amount_source: str = Field(
        default="settlement_ledger.net_settlement_amount",
        description="Where the amount came from — always settlement ledger",
    )
    approval_required: bool = Field(
        default=True,
        description="Manual payout ALWAYS requires human approval",
    )
    execution_mode: str = Field(
        default="draft_only",
        description="NEVER 'execute' — always 'draft_only'",
    )
    duplicate_payout_risk: bool = Field(
        default=False,
        description="True if an existing payout is processing/success",
    )
    safety_notes: list[str] = Field(
        default_factory=lambda: [
            "Không tự động thực hiện payout",
            "Số tiền lấy từ settlement_ledger, không từ merchant",
            "Cần phê duyệt trước khi thực hiện",
            "Kiểm tra bank account verified trước khi duyệt",
        ],
    )
    evidence_summary: list[str] = Field(
        default_factory=list,
        description="Must not be empty — reviewer needs evidence",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UncEmailDraft(BaseModel):
    """Draft UNC email to merchant — requires approval, does not send real email."""

    case_id: str = Field(..., min_length=1)
    merchant_id: str = Field(..., min_length=1)
    payout_id: str | None = None
    unc_number: str | None = None
    receipt_url: str | None = None
    merchant_email: str | None = None
    reason: str = Field(default="Gửi UNC/biên lai chuyển khoản cho merchant")
    execution_mode: str = Field(default="draft_only")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BankAccountCorrectionDraft(BaseModel):
    """Draft request for merchant to correct bank account — no bank update."""

    case_id: str = Field(..., min_length=1)
    merchant_id: str = Field(..., min_length=1)
    bank_account_id: str | None = None
    correction_reason: str = Field(..., min_length=1)
    execution_mode: str = Field(default="draft_only")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SettlementStatementDraft(BaseModel):
    """Draft settlement statement for merchant — read-only summary."""

    case_id: str = Field(..., min_length=1)
    merchant_id: str = Field(..., min_length=1)
    settlement_date: str | None = None
    gross_amount: int | None = None
    fee_amount: int | None = None
    refund_amount: int | None = None
    chargeback_amount: int | None = None
    net_settlement_amount: int | None = None
    currency: str = Field(default="VND")
    reason: str = Field(default="Net settlement = 0 hoặc âm, gửi sao kê cho merchant")
    execution_mode: str = Field(default="draft_only")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MerchantEmailDraft(BaseModel):
    """Generic draft merchant email — does not send real email."""

    case_id: str = Field(..., min_length=1)
    merchant_id: str = Field(..., min_length=1)
    subject: str = Field(default="")
    body: str = Field(default="")
    merchant_email: str | None = None
    execution_mode: str = Field(default="draft_only")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

