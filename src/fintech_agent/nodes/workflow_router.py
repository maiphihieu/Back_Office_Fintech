"""Node: workflow_router — route to the correct workflow."""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.schemas.enums import AuditEventType, CaseStatus


def route_workflow(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Route the case to train_ticket or utility_bill workflow.

    If service type is unknown, route to manual_review.
    """
    workflow = state.get("selected_workflow")
    case_id = state.get("case_id", "")
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    if workflow in ("train_ticket", "utility_bill", "wallet_topup", "fraud_account_lock", "merchant_settlement_delay"):
        if audit:
            ev = audit.log_event(
                case_id, AuditEventType.WORKFLOW_ROUTED,
                details={"workflow": workflow},
                previous_status=state.get("status", CaseStatus.ROUTED).value if isinstance(state.get("status"), CaseStatus) else str(state.get("status", "")),
                new_status=CaseStatus.RULE_DECISION.value,
            )
            audit_ids.append(ev.event_id)
        return {
            "status": CaseStatus.RULE_DECISION,
            "audit_event_ids": audit_ids,
        }

    if audit:
        ev = audit.log_event(
            case_id, AuditEventType.WORKFLOW_ROUTED,
            details={"workflow": "manual_review", "reason": "unknown service type"},
            new_status=CaseStatus.MANUAL_REVIEW.value,
        )
        audit_ids.append(ev.event_id)

    return {
        "status": CaseStatus.MANUAL_REVIEW,
        "errors": [*state.get("errors", []), "unknown service type, cannot route"],
        "audit_event_ids": audit_ids,
    }
