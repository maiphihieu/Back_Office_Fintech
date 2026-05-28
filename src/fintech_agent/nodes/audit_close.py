"""Node: audit_close — log final audit event and close/pause the case.

If status is WAITING_APPROVAL, the case pauses (not closed).
Otherwise, sets CLOSED.
"""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.schemas.enums import AuditEventType, CaseStatus


def audit_and_close(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Log audit event and finalize case status.

    If status is WAITING_APPROVAL → keep it (case is paused, not closed).
    Otherwise → set CLOSED.
    """
    case_id = state.get("case_id", "")
    audit_ids: list[str] = list(state.get("audit_event_ids", []))
    prev_status = state.get("status")

    # Don't close if waiting for approval — the case is paused
    if prev_status == CaseStatus.WAITING_APPROVAL:
        if audit:
            ev = audit.log_event(
                case_id, AuditEventType.WORKFLOW_ROUTED,
                actor="system",
                new_status=CaseStatus.WAITING_APPROVAL.value,
                details={"note": "case paused, waiting for human approval"},
            )
            audit_ids.append(ev.event_id)
        return {
            "status": CaseStatus.WAITING_APPROVAL,
            "audit_event_ids": audit_ids,
        }

    # Normal close
    if audit:
        ev = audit.log_event(
            case_id, AuditEventType.CASE_CLOSED,
            actor="system",
            previous_status=prev_status.value if isinstance(prev_status, CaseStatus) else str(prev_status or ""),
            new_status=CaseStatus.CLOSED.value,
            details={
                "draft_type": state.get("draft_output", {}).get("type", "none"),
                "error_count": len(state.get("errors", [])),
            },
        )
        audit_ids.append(ev.event_id)

    return {
        "status": CaseStatus.CLOSED,
        "audit_event_ids": audit_ids,
    }
