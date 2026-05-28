"""Approval gate models.

ApprovalPacket intentionally does NOT contain model_confidence
to avoid biasing the human reviewer.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, model_validator

from fintech_agent.schemas.enums import ActionType, ApprovalStatus, RiskLevel


class ApprovalPacket(BaseModel):
    """Packet sent to a human reviewer for approval.

    Design decisions:
      - No model_confidence field — reviewer must evaluate on evidence alone.
      - evidence_summary must be non-empty if action involves refund.
      - escalate_to indicates who should review if primary approver times out.
    """

    case_id: str = Field(..., min_length=1)
    proposed_action: ActionType
    amount: int = Field(..., ge=0)
    transaction_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    reason: str
    evidence_summary: list[str] = Field(default_factory=list)
    risk_level: RiskLevel
    rule_version: str = "1.0.0"
    requires_approval: bool = True
    approval_deadline: datetime | None = None
    escalate_to: str | None = None

    @model_validator(mode="after")
    def refund_must_have_evidence(self) -> "ApprovalPacket":
        """Refund actions must have non-empty evidence for the reviewer."""
        if (
            self.proposed_action == ActionType.CREATE_REFUND_REQUEST_DRAFT
            and len(self.evidence_summary) == 0
        ):
            raise ValueError(
                "evidence_summary must not be empty for refund-related actions"
            )
        return self


class ApprovalDecision(BaseModel):
    """Human reviewer's decision on an approval packet."""

    case_id: str = Field(..., min_length=1)
    approver: str = Field(..., min_length=1)
    status: ApprovalStatus
    comment: str | None = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
