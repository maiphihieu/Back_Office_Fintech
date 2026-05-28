"""Node: missing_info — handle cases with incomplete extraction."""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.schemas.enums import AuditEventType, CaseStatus


def missing_info_handler(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Handle missing information.

    transaction_id missing → dead_letter (cannot proceed).
    Other missing fields → proceed with what we have.
    """
    missing = state.get("missing_info", [])
    case_id = state.get("case_id", "")
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    if "transaction_id" in missing:
        if audit:
            ev = audit.log_event(
                case_id, AuditEventType.DEAD_LETTER_CREATED,
                actor="system",
                details={"reason": "transaction_id missing", "missing": missing},
                new_status=CaseStatus.DEAD_LETTER.value,
            )
            audit_ids.append(ev.event_id)
        return {
            "status": CaseStatus.DEAD_LETTER,
            "errors": [*state.get("errors", []), "critical: transaction_id missing"],
            "audit_event_ids": audit_ids,
        }

    if audit:
        ev = audit.log_event(
            case_id, AuditEventType.MISSING_INFO_DETECTED,
            details={"missing": missing, "action": "proceed_with_partial"},
        )
        audit_ids.append(ev.event_id)

    return {
        "status": CaseStatus.FETCHING_EVIDENCE,
        "audit_event_ids": audit_ids,
    }
