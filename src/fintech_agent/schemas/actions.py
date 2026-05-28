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
