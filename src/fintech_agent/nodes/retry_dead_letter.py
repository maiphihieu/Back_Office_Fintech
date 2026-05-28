"""Node: retry_dead_letter — handle tool failures with retry logic."""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.schemas.enums import AuditEventType, CaseStatus


def retry_or_dead_letter(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Decide whether to retry (up to max_retries) or dead-letter.

    max_retries defaults to 3.
    """
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)
    case_id = state.get("case_id", "")
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    if retry_count < max_retries:
        if audit:
            ev = audit.log_event(
                case_id, AuditEventType.RETRY_SCHEDULED,
                details={"retry_count": retry_count + 1, "max_retries": max_retries},
            )
            audit_ids.append(ev.event_id)
        return {
            "retry_count": retry_count + 1,
            "status": CaseStatus.FETCHING_EVIDENCE,
            "audit_event_ids": audit_ids,
        }

    if audit:
        ev = audit.log_event(
            case_id, AuditEventType.DEAD_LETTER_CREATED,
            actor="system",
            details={"reason": "max retries exceeded", "retry_count": retry_count},
            new_status=CaseStatus.DEAD_LETTER.value,
        )
        audit_ids.append(ev.event_id)

    return {
        "status": CaseStatus.DEAD_LETTER,
        "errors": [*state.get("errors", []), "max retries exceeded"],
        "audit_event_ids": audit_ids,
    }
