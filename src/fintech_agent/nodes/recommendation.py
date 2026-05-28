"""Node: recommendation — build a RecommendedAction from rule decision."""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.rules.risk_rules import classify_risk
from fintech_agent.schemas.actions import RecommendedAction
from fintech_agent.schemas.enums import ActionType, AuditEventType, CaseStatus


def recommend_action(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Build a RecommendedAction from rule_decision with risk classification."""
    decision = state.get("rule_decision")
    case_id = state.get("case_id", "")
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    if not decision:
        return {
            "status": CaseStatus.MANUAL_REVIEW,
            "errors": [*state.get("errors", []), "no rule_decision available"],
            "audit_event_ids": audit_ids,
        }

    action_type = ActionType(decision["action"])
    evidence = state.get("evidence_bundle")
    amount = evidence.wallet_ledger.debit_amount if evidence and evidence.wallet_ledger else 0
    risk = classify_risk(action_type, amount)

    recommended = RecommendedAction(
        action_type=action_type,
        diagnosis=decision["diagnosis"],
        summary=f"Rule engine recommends: {action_type.value} ({decision['diagnosis']})",
        risk_level=risk,
        approval_required=decision.get("approval_required", False),
    )

    next_status = (
        CaseStatus.WAITING_APPROVAL
        if decision.get("approval_required")
        else CaseStatus.DRAFT_CREATED
    )

    if audit:
        ev = audit.log_action_recommended(
            case_id,
            action_type=action_type.value,
            diagnosis=decision["diagnosis"],
            approval_required=decision.get("approval_required", False),
        )
        audit_ids.append(ev.event_id)

    return {
        "recommended_action": recommended,
        "status": next_status,
        "audit_event_ids": audit_ids,
    }
