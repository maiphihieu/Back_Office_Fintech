"""Node: conflict_detection — run conflict rules on evidence."""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.rules.conflict_rules import detect_all_conflicts
from fintech_agent.schemas.enums import AuditEventType, CaseStatus
from fintech_agent.schemas.evidence import EvidenceBundle


def detect_conflict(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Run conflict detection rules on evidence bundle."""
    evidence = state.get("evidence_bundle") or EvidenceBundle()
    user_id = state.get("user_id")
    case_id = state.get("case_id", "")
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    conflicts = detect_all_conflicts(evidence, case_user_id=user_id)
    updated_evidence = evidence.model_copy(update={"conflicts": conflicts})
    has_conflict = len(conflicts) > 0

    next_status = CaseStatus.CONFLICT_DETECTED if has_conflict else CaseStatus.ROUTED

    if audit and has_conflict:
        ev = audit.log_event(
            case_id, AuditEventType.CONFLICT_DETECTED,
            details={
                "conflict_count": len(conflicts),
                "conflicts": [c.description for c in conflicts],
            },
            new_status=next_status.value,
        )
        audit_ids.append(ev.event_id)

    return {
        "evidence_bundle": updated_evidence,
        "conflicts": conflicts,
        "has_conflict": has_conflict,
        "status": next_status,
        "audit_event_ids": audit_ids,
    }
