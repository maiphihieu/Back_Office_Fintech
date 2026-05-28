"""Node: approval_gate — create ApprovalPacket and pause for human review.

When approval is required:
  1. Creates an ApprovalPacket with all evidence context
  2. Sets status = WAITING_APPROVAL, approval_status = PENDING
  3. Graph stops here — resumes when ApprovalService calls approve/reject

When approval is not required:
  1. Sets approval_status = NOT_REQUIRED
  2. Graph continues to create_draft
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.schemas.approval import ApprovalPacket
from fintech_agent.schemas.enums import (
    ActionType,
    ApprovalStatus,
    AuditEventType,
    CaseStatus,
    RiskLevel,
)


def approval_gate(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Create ApprovalPacket and set PENDING for human review."""
    case_id = state.get("case_id", "")
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    if not state.get("approval_required", False):
        return {
            "approval_status": ApprovalStatus.NOT_REQUIRED,
            "status": CaseStatus.DRAFT_CREATED,
            "audit_event_ids": audit_ids,
        }

    # Build the ApprovalPacket
    action = state.get("recommended_action")
    evidence = state.get("evidence_bundle")
    extracted = state.get("extracted_info")
    rule_decision = state.get("rule_decision", {})

    action_type = ActionType(rule_decision.get("action", "manual_review"))
    amount = evidence.wallet_ledger.debit_amount if evidence and evidence.wallet_ledger else 0
    risk = RiskLevel(action.risk_level) if action else RiskLevel.HIGH

    packet = ApprovalPacket(
        case_id=case_id,
        proposed_action=action_type,
        amount=amount,
        transaction_id=extracted.transaction_id if extracted and extracted.transaction_id else "UNKNOWN",
        user_id=state.get("user_id") or "UNKNOWN",
        reason=rule_decision.get("diagnosis", "unknown"),
        evidence_summary=[
            rule_decision.get("diagnosis", ""),
            f"amount={amount}",
            f"risk={risk.value}",
            f"workflow={state.get('selected_workflow', 'unknown')}",
        ],
        risk_level=risk,
        rule_version="1.0.0",
        requires_approval=True,
        approval_deadline=datetime.now(UTC) + timedelta(hours=24),
    )

    # Log approval request
    if audit:
        ev = audit.log_event(
            case_id, AuditEventType.APPROVAL_REQUESTED,
            actor="system",
            details={
                "action": action_type.value,
                "amount": amount,
                "risk": risk.value,
                "transaction_id": packet.transaction_id,
            },
            new_status=CaseStatus.WAITING_APPROVAL.value,
        )
        audit_ids.append(ev.event_id)

    return {
        "approval_packet": packet,
        "approval_status": ApprovalStatus.PENDING,
        "status": CaseStatus.WAITING_APPROVAL,
        "audit_event_ids": audit_ids,
    }
