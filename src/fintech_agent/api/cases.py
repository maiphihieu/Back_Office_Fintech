"""Case management endpoints.

POST /cases                    — create + run a new case
GET  /cases                    — list all cases
GET  /cases/{case_id}          — get case status/details
GET  /cases/{case_id}/audit    — get audit trail
POST /cases/{case_id}/approve  — approve pending action
POST /cases/{case_id}/reject   — reject pending action
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from fintech_agent.api.models import (
    ApprovalPacketResponse,
    ApproveRequest,
    AuditEventResponse,
    AuditTrailResponse,
    CaseListResponse,
    CaseResponse,
    ConflictResponse,
    CreateCaseRequest,
    ErrorResponse,
    EvidenceBundleResponse,
    ExtractedInfoResponse,
    RejectRequest,
)
from fintech_agent.api.service import get_case_service
from fintech_agent.graph.state import AgentState
from fintech_agent.messages.wallet_topup_messages import get_cs_message
from fintech_agent.schemas.enums import ActionType
from fintech_agent.schemas.enums import CaseStatus
from fintech_agent.workflows.approval_service import (
    AlreadyDecidedError,
    CaseNotFoundError,
)

router = APIRouter(prefix="/cases", tags=["cases"])


# ─── Helpers ───────────────────────────────────────────────

def _enum_val(v):
    """Safely extract .value from enum or return str."""
    if v is None:
        return None
    return v.value if hasattr(v, "value") else str(v)


def _serialize_model(obj) -> dict | None:
    """Serialize a Pydantic model or dict to plain dict."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        d = obj.model_dump()
        # Convert enums to strings
        return {k: (_enum_val(v) if hasattr(v, "value") else v) for k, v in d.items()}
    if isinstance(obj, dict):
        return {k: (_enum_val(v) if hasattr(v, "value") else v) for k, v in obj.items()}
    return None


def _state_to_response(state: AgentState) -> CaseResponse:
    """Convert internal AgentState to API response with full details."""
    action = state.get("recommended_action")
    decision = state.get("rule_decision", {})
    status = state.get("status", CaseStatus.NEW)
    status_val = status.value if isinstance(status, CaseStatus) else str(status)

    # Determine next step hint
    if status_val == "waiting_approval":
        next_step = "POST /cases/{case_id}/approve or /reject"
    elif status_val == "closed":
        next_step = "Case is closed. Use GET /cases/{case_id}/audit to review."
    elif status_val == "manual_review":
        next_step = "Case requires manual review by ops team."
    else:
        next_step = f"Current status: {status_val}"

    # Extract recommended_action from rule_decision or RecommendedAction
    recommended_action = None
    if decision and isinstance(decision, dict):
        recommended_action = decision.get("action")
    elif action:
        recommended_action = action.action_type.value if hasattr(action, "action_type") else str(action)

    # Risk level
    risk_level = None
    if action and hasattr(action, "risk_level"):
        risk_level = _enum_val(action.risk_level)
    elif decision and isinstance(decision, dict):
        risk_level = decision.get("risk_level")

    # Extracted info
    extracted_info_resp = None
    ei = state.get("extracted_info")
    if ei:
        ei_data = _serialize_model(ei) or {}
        extracted_info_resp = ExtractedInfoResponse(**{
            k: v for k, v in ei_data.items()
            if k in ExtractedInfoResponse.model_fields
        })

    # Evidence bundle
    evidence_resp = None
    eb = state.get("evidence_bundle")
    if eb:
        evidence_resp = EvidenceBundleResponse(
            transaction=_serialize_model(getattr(eb, "transaction", None)),
            wallet_ledger=_serialize_model(getattr(eb, "wallet_ledger", None)),
            provider_status=_serialize_model(getattr(eb, "provider_status", None)),
            refund_status=_serialize_model(getattr(eb, "refund_status", None)),
            reconciliation_status=_serialize_model(
                getattr(eb, "reconciliation_status", None)
            ),
        )

    # Conflicts
    conflicts_raw = state.get("conflicts", [])
    conflicts = []
    for c in conflicts_raw:
        if hasattr(c, "conflict_type"):
            conflicts.append(ConflictResponse(
                conflict_type=_enum_val(c.conflict_type) or str(c.conflict_type),
                description=c.description if hasattr(c, "description") else str(c),
                source_a=getattr(c, "source_a", None),
                source_b=getattr(c, "source_b", None),
            ))
        elif isinstance(c, dict):
            conflicts.append(ConflictResponse(
                conflict_type=c.get("conflict_type", "unknown"),
                description=c.get("description", ""),
                source_a=c.get("source_a"),
                source_b=c.get("source_b"),
            ))

    # Audit event count
    audit_ids = state.get("audit_event_ids", [])

    # Diagnosis message (human-readable) for wallet_topup
    diagnosis_raw = decision.get("diagnosis") if isinstance(decision, dict) else None
    diagnosis_message = None
    if state.get("selected_workflow") == "wallet_topup" and diagnosis_raw and recommended_action:
        try:
            diagnosis_message = get_cs_message(ActionType(recommended_action), diagnosis_raw)
        except (ValueError, KeyError):
            pass

    return CaseResponse(
        case_id=state.get("case_id", ""),
        status=status_val,
        user_id=state.get("user_id"),
        selected_workflow=state.get("selected_workflow"),
        recommended_action=recommended_action,
        diagnosis=diagnosis_raw,
        diagnosis_message=diagnosis_message,
        risk_level=risk_level,
        approval_required=state.get("approval_required", False),
        approval_status=_enum_val(state.get("approval_status")),
        has_conflict=state.get("has_conflict", False),
        conflicts=conflicts,
        extracted_info=extracted_info_resp,
        evidence=evidence_resp,
        draft_output=state.get("draft_output"),
        errors=state.get("errors", []),
        next_step=next_step,
        raw_complaint=state.get("raw_complaint"),
        audit_event_count=len(audit_ids),
    )


# ─── Endpoints ─────────────────────────────────────────────

@router.get(
    "",
    response_model=CaseListResponse,
    summary="List all cases",
)
async def list_cases() -> CaseListResponse:
    """List all cases in the system (MVP: in-memory)."""
    svc = get_case_service()
    cases = []
    for case_id in svc._cases:
        state = svc.get_case(case_id)
        if state:
            cases.append(_state_to_response(state))
    return CaseListResponse(total=len(cases), cases=cases)


@router.post(
    "",
    response_model=CaseResponse,
    status_code=201,
    summary="Create and run a new case",
    responses={422: {"model": ErrorResponse}},
)
async def create_case(req: CreateCaseRequest) -> CaseResponse:
    """Create a new complaint case and execute the workflow graph.

    The graph runs Phase 1 (evidence → rules → recommendation).
    If approval is required, the case pauses at `waiting_approval`.
    """
    svc = get_case_service()
    state = svc.create_and_run(
        raw_complaint=req.raw_complaint,
        user_id=req.user_id,
        transaction_id=req.transaction_id,
        service_type=req.service_type,
    )
    return _state_to_response(state)


@router.get(
    "/{case_id}",
    response_model=CaseResponse,
    summary="Get case details",
    responses={404: {"model": ErrorResponse}},
)
async def get_case(case_id: str) -> CaseResponse:
    """Retrieve current state of a case."""
    svc = get_case_service()
    state = svc.get_case(case_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")
    return _state_to_response(state)


@router.get(
    "/{case_id}/audit",
    response_model=AuditTrailResponse,
    summary="Get audit trail for a case",
    responses={404: {"model": ErrorResponse}},
)
async def get_audit_trail(case_id: str) -> AuditTrailResponse:
    """Retrieve the full audit trail for a case."""
    svc = get_case_service()
    state = svc.get_case(case_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    events = svc.get_audit_trail(case_id)
    return AuditTrailResponse(
        case_id=case_id,
        event_count=len(events),
        events=[AuditEventResponse(**e) for e in events],
    )


@router.post(
    "/{case_id}/approve",
    response_model=CaseResponse,
    summary="Approve a pending case",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def approve_case(case_id: str, req: ApproveRequest) -> CaseResponse:
    """Approve a case waiting for approval.

    Triggers Phase 2: creates the draft action and closes the case.
    """
    svc = get_case_service()
    try:
        state = svc.approve_case(case_id, req.approver, req.comment)
    except CaseNotFoundError:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found or not pending")
    except AlreadyDecidedError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _state_to_response(state)


@router.post(
    "/{case_id}/reject",
    response_model=CaseResponse,
    summary="Reject a pending case",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def reject_case(case_id: str, req: RejectRequest) -> CaseResponse:
    """Reject a case waiting for approval.

    No draft is created. Case is closed with rejection.
    """
    svc = get_case_service()
    try:
        state = svc.reject_case(case_id, req.approver, req.reason)
    except CaseNotFoundError:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found or not pending")
    except AlreadyDecidedError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _state_to_response(state)
