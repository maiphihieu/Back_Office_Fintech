"""Node: manual_review — route to human for manual resolution."""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.rules.risk_rules import classify_risk
from fintech_agent.schemas.actions import RecommendedAction
from fintech_agent.schemas.enums import ActionType, AuditEventType, CaseStatus, RiskLevel


def manual_review(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Set case to manual review with context for ops team."""
    case_id = state.get("case_id", "")
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    conflicts = state.get("conflicts", [])
    errors = state.get("errors", [])

    draft_output = {
        "type": "manual_review",
        "reason": errors,
        "conflicts": [c.description for c in conflicts] if conflicts else [],
        "status": "pending_human",
    }

    # Build a RecommendedAction so the API response is complete
    evidence = state.get("evidence_bundle")
    amount = evidence.wallet_ledger.debit_amount if evidence and evidence.wallet_ledger else 0
    risk = classify_risk(ActionType.MANUAL_REVIEW, amount)

    recommended = RecommendedAction(
        action_type=ActionType.MANUAL_REVIEW,
        diagnosis="Conflict detected — requires manual review by ops team",
        summary=f"Manual review required: {len(conflicts)} conflict(s) found",
        risk_level=risk,
        approval_required=True,
    )

    if audit:
        ev = audit.log_event(
            case_id, AuditEventType.WORKFLOW_ROUTED,
            details={
                "target": "manual_review",
                "conflict_count": len(conflicts),
                "error_count": len(errors),
            },
            new_status=CaseStatus.MANUAL_REVIEW.value,
        )
        audit_ids.append(ev.event_id)

    return {
        "status": CaseStatus.MANUAL_REVIEW,
        "draft_output": draft_output,
        "recommended_action": recommended,
        "approval_required": True,
        "rule_decision": {
            "action": ActionType.MANUAL_REVIEW.value,
            "diagnosis": "Conflict detected — requires manual review by ops team",
            "approval_required": True,
        },
        "audit_event_ids": audit_ids,
    }

