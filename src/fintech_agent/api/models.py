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


class EvidenceBundleResponse(BaseModel):
    """Evidence bundle summary for the case detail view."""
    transaction: dict | None = None
    wallet_ledger: dict | None = None
    provider_status: dict | None = None
    refund_status: dict | None = None
    reconciliation_status: dict | None = None


class ConflictResponse(BaseModel):
    """Single evidence conflict."""
    conflict_type: str
    description: str
    source_a: str | None = None
    source_b: str | None = None


class CaseResponse(BaseModel):
    """Standard case response — no raw PII."""
    case_id: str
    status: str
    user_id: str | None = None
    selected_workflow: str | None = None
    recommended_action: str | None = None
    diagnosis: str | None = None
    risk_level: str | None = None
    approval_required: bool = False
    approval_status: str | None = None
    has_conflict: bool = False
    conflicts: list[ConflictResponse] = Field(default_factory=list)
    extracted_info: ExtractedInfoResponse | None = None
    evidence: EvidenceBundleResponse | None = None
    draft_output: dict | None = None
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
