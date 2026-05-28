"""Node: case_intake — entry point for a new complaint.

Initializes the agent state and logs case_received audit event.
"""

from __future__ import annotations

from uuid import uuid4

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.schemas.enums import AuditEventType, CaseStatus


def case_intake(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Initialize case state from raw complaint input.

    Sets case_id, status, retry state, correlation_id.
    Logs case_received audit event.
    """
    case_id = state.get("case_id") or f"CASE_{uuid4().hex[:8].upper()}"
    corr_id = state.get("correlation_id") or uuid4().hex[:12]

    audit_ids: list[str] = list(state.get("audit_event_ids", []))
    if audit:
        ev = audit.log_case_received(case_id, state.get("raw_complaint", ""))
        audit_ids.append(ev.event_id)

    return {
        "case_id": case_id,
        "status": CaseStatus.EXTRACTING,
        "retry_count": state.get("retry_count", 0),
        "max_retries": state.get("max_retries", 3),
        "errors": [],
        "audit_event_ids": audit_ids,
        "correlation_id": corr_id,
    }
