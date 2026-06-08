"""API request/response models — thin Pydantic schemas for the REST layer.

These models decouple the internal AgentState from the API surface.
No PII is exposed in responses unless explicitly needed.
No secrets (API keys) are ever included in responses.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ─── Requests ──────────────────────────────────────────────

class CreateCaseRequest(BaseModel):
    """POST /cases — create and run a new case."""
    raw_complaint: str = Field(..., min_length=1, description="Customer complaint text")
    user_id: str | None = Field(None, description="User ID (optional, extracted if missing)")
    transaction_id: str | None = Field(None, description="Transaction ID (optional, extracted if missing)")
    service_type: str | None = Field(None, description="Service type hint: train_ticket, electric_bill, water_bill")


class ApproveRequest(BaseModel):
    """POST /cases/{case_id}/approve"""
    approver: str = Field(..., min_length=1, description="ID or name of the approver")
    comment: str | None = Field(None, description="Optional approval comment")


class RejectRequest(BaseModel):
    """POST /cases/{case_id}/reject"""
    approver: str = Field(..., min_length=1, description="ID or name of the reviewer")
    reason: str = Field(..., min_length=1, description="Reason for rejection")


# ─── Responses ─────────────────────────────────────────────

class ExtractedInfoResponse(BaseModel):
    """Extracted info from complaint text."""
    user_id: str | None = None
    transaction_id: str | None = None
    service_type: str | None = None
    issue_type: str | None = None
    order_id: str | None = None
    bill_code: str | None = None
    customer_code: str | None = None
    amount_claimed: int | None = None
    language: str | None = None
    confidence: float | None = None
    extraction_method: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    # Merchant settlement fields
    merchant_id: str | None = None
    merchant_name: str | None = None
    tax_code: str | None = None
    settlement_cycle: str | None = None
    settlement_date: str | None = None
    payout_id: str | None = None
    batch_id: str | None = None


class EvidenceBundleResponse(BaseModel):
    """Evidence bundle summary for the case detail view."""
    transaction: dict | None = None
    wallet_ledger: dict | None = None
    provider_status: dict | None = None
    refund_status: dict | None = None
    reconciliation_status: dict | None = None
    # Merchant settlement evidence
    merchant_profile: dict | None = None
    merchant_bank_account: dict | None = None
    merchant_settlement_ledger: dict | None = None
    settlement_batch: dict | None = None
    merchant_payout: dict | None = None
    bank_transfer_receipt: dict | None = None


class ConflictResponse(BaseModel):
    """Single evidence conflict."""
    conflict_type: str
    description: str
    source_a: str | None = None
    source_b: str | None = None


class ResponseDebugData(BaseModel):
    """Debug/observability for generated response."""
    generation_mode: str = "fallback"
    fallback_reason: str | None = None
    llm_error: str | None = None
    model_used: str | None = None


class GeneratedResponseData(BaseModel):
    """LLM-generated case summary for CS/Ops staff."""
    case_summary: str = ""
    problem_location: str = ""
    problem_explanation: str = ""
    evidence_checked: list[str] = Field(default_factory=list)
    evidence_supporting_problem_location: list[str] = Field(default_factory=list)
    problem_location_confidence: str = ""
    internal_summary: str = ""
    recommended_next_step: str = ""
    customer_reply_draft: str = ""
    safety_notes: list[str] = Field(default_factory=list)
    debug: ResponseDebugData | None = None


class TicketActionData(BaseModel):
    """Single recommended action within a resolution ticket."""
    action_id: str = ""
    action_name: str = ""
    action_type: str = ""
    description: str = ""
    mcp_tool: str | None = None
    mcp_input: dict | None = None
    preconditions: list[str] = Field(default_factory=list)
    evidence_dependencies: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    approval_status: str = "not_required"
    execution_mode: str = "manual"
    risk_level: str = "unknown"
    reason: str = ""
    status: str = "manual_required"
    expected_result: str = ""
    safety_notes: list[str] = Field(default_factory=list)
    staff_instruction: str = ""


class AmountVerificationData(BaseModel):
    """Amount verification metadata for API response."""
    customer_claimed_amount: int | None = None
    trusted_amount: int | None = None
    trusted_amount_source: str | None = None
    action_amount: int | None = None
    action_amount_source: str | None = None
    has_amount_mismatch: bool = False
    mismatch_description: str = ""


class ClaimVerificationData(BaseModel):
    """Single claim verification result for API response."""
    claim_id: str = ""
    claim_type: str = "unknown_claim"
    raw_text: str = ""
    customer_claimed_value: str | int | float | None = None
    normalized_value: str | int | float | None = None
    unit: str | None = None
    confidence: float = 0.0
    verification_status: str = "not_verifiable"
    trusted_system_value: str | int | float | None = None
    trusted_source: str | None = None
    explanation: str = ""


class ClaimVerificationSummaryData(BaseModel):
    """Aggregate claim verification summary for API response."""
    summary: str = ""
    claims: list[ClaimVerificationData] = Field(default_factory=list)
    matched_claims: list[str] = Field(default_factory=list)
    mismatched_claims: list[str] = Field(default_factory=list)
    not_verifiable_claims: list[str] = Field(default_factory=list)
    not_found_claims: list[str] = Field(default_factory=list)
    has_customer_detail_mismatch: bool = False
    has_system_evidence_conflict: bool = False
    staff_explanation: str = ""
    trusted_data_used_for_action: dict[str, str | int | float | None] = Field(
        default_factory=dict,
    )


class ResolutionTicketData(BaseModel):
    """Full resolution ticket for API response."""
    ticket_id: str = ""
    ticket_type: str = "unknown"
    issue_summary: str = ""
    problem_location: str = "unknown"
    problem_explanation: str = ""
    evidence_checked: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    resolution_status: str = "not_supported"
    recommended_actions: list[TicketActionData] = Field(default_factory=list)
    staff_instruction: str = ""
    customer_reply_draft: str = ""
    safety_notes: list[str] = Field(default_factory=list)
    amount_verification: AmountVerificationData | None = None
    claim_verification: ClaimVerificationSummaryData | None = None


class CaseResponse(BaseModel):
    """Standard case response — no raw PII."""
    case_id: str
    status: str
    user_id: str | None = None
    selected_workflow: str | None = None
    recommended_action: str | None = None
    diagnosis: str | None = None
    diagnosis_message: str | None = None
    risk_level: str | None = None
    approval_required: bool = False
    approval_status: str | None = None
    has_conflict: bool = False
    conflicts: list[ConflictResponse] = Field(default_factory=list)
    extracted_info: ExtractedInfoResponse | None = None
    evidence: EvidenceBundleResponse | None = None
    draft_output: dict | None = None
    generated_response: GeneratedResponseData | None = None
    resolution_ticket: ResolutionTicketData | None = None
    errors: list[str] = Field(default_factory=list)
    next_step: str = ""
    raw_complaint: str | None = None
    audit_event_count: int = 0


class CaseListResponse(BaseModel):
    """Response for GET /cases list."""
    total: int
    cases: list[CaseResponse]


class ApprovalPacketResponse(BaseModel):
    """Approval packet summary for reviewers."""
    case_id: str
    proposed_action: str
    amount: int
    transaction_id: str
    risk_level: str
    reason: str
    evidence_summary: list[str] = Field(default_factory=list)
    status: str = "pending"


class AuditEventResponse(BaseModel):
    """Single audit event."""
    event_id: str
    event_type: str
    actor: str
    timestamp: str
    previous_status: str | None = None
    new_status: str | None = None
    details: dict = Field(default_factory=dict)


class AuditTrailResponse(BaseModel):
    """Full audit trail for a case."""
    case_id: str
    event_count: int
    events: list[AuditEventResponse]


class ErrorResponse(BaseModel):
    """Standard error response."""
    error: str
    detail: str | None = None
