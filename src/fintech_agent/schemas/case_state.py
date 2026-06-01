"""CaseState — the central state object flowing through the LangGraph workflow.

CaseState is the single source of truth for a case's lifecycle.
Every graph node reads and updates fields on this object.
"""

from __future__ import annotations

from typing import Any

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

from fintech_agent.schemas.enums import (
    ActionType,
    ApprovalStatus,
    CaseStatus,
    IssueType,
    RiskLevel,
    ServiceType,
)
from fintech_agent.schemas.evidence import EvidenceBundle
from fintech_agent.schemas.response_generation import GeneratedResponse


class ExtractedInfo(BaseModel):
    """Structured info extracted from the raw complaint by the LLM node.

    SAFETY NOTE:
        amount_claimed is the customer's *stated* amount from the complaint.
        It MUST NEVER be used as the refund amount. The refund amount always
        comes from wallet_ledger.debit_amount (source of truth).
    """

    user_id: str | None = None
    transaction_id: str | None = None
    service_type: ServiceType | None = None
    issue_type: IssueType | None = None
    order_id: str | None = None
    bill_code: str | None = None
    customer_code: str | None = None

    # --- Identity fields (Phase 1 — fraud/account lock) ---
    phone: str | None = None
    email: str | None = None
    wallet_id: str | None = None

    # --- LLM extraction extras (Phase 2) ---
    amount_claimed: int | None = Field(default=None, ge=0)
    language: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    extraction_method: str | None = None  # "mock_regex" | "openai_llm" | "fallback_regex"
    missing_fields: list[str] = Field(default_factory=list)

    # --- Generic claims (Phase 3 — scalable claim framework) ---
    claims: list[Claim] = Field(
        default_factory=list,
        description=(
            "Generic claims extracted from complaint text. "
            "Each claim has a claim_type, customer_claimed_value, "
            "and gets verified against system evidence."
        ),
    )


class CaseState(BaseModel):
    """Central state object for a complaint case.

    This flows through the LangGraph state machine:
        NEW → EXTRACTING → FETCHING_EVIDENCE → ... → CLOSED

    Key invariants:
      - current_state transitions must follow the state machine.
      - reopen_count has a hard cap (default 3).
      - evidence is the aggregated bundle from all tool calls.
    """

    # --- Identity ---
    case_id: str = Field(..., min_length=1)
    ticket_id: str = Field(..., min_length=1)

    # --- State machine ---
    current_state: CaseStatus = CaseStatus.NEW
    previous_state: CaseStatus | None = None

    # --- Input ---
    raw_complaint: str = ""

    # --- Extracted info (from LLM) ---
    extracted_info: ExtractedInfo = Field(default_factory=ExtractedInfo)
    missing_fields: list[str] = Field(default_factory=list)

    # --- Workflow routing ---
    selected_workflow: str | None = None

    # --- Evidence ---
    evidence: EvidenceBundle = Field(default_factory=EvidenceBundle)

    # --- Diagnosis & recommendation ---
    diagnosis: str | None = None
    recommended_action: ActionType | None = None
    risk_level: RiskLevel | None = None

    # --- Approval ---
    approval_required: bool = False
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED
    approval_deadline: datetime | None = None

    # --- Re-open ---
    reopen_count: int = Field(default=0, ge=0)
    max_reopen: int = Field(default=3, ge=1)
    reopen_reason: str | None = None

    # --- Timestamps ---
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # --- LLM generated response ---
    generated_response: GeneratedResponse | None = None

    # --- Error tracking ---
    error_message: str | None = None

    def transition_to(self, new_state: CaseStatus) -> None:
        """Transition to a new state, recording the previous one."""
        self.previous_state = self.current_state
        self.current_state = new_state
        self.updated_at = datetime.now(UTC)

    @property
    def can_reopen(self) -> bool:
        """Check if the case can be re-opened."""
        return (
            self.current_state == CaseStatus.CLOSED
            and self.reopen_count < self.max_reopen
        )


# ── Deferred model rebuild for forward references ────────────
# Claim is defined in claim_verification.py. We import it here
# (after all classes are defined) to resolve the forward reference.
from fintech_agent.schemas.claim_verification import Claim  # noqa: E402

ExtractedInfo.model_rebuild()
