"""Node: rule_decision — apply workflow-specific deterministic rules."""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.rules.train_ticket_rules import decide_train_ticket
from fintech_agent.rules.utility_bill_rules import decide_utility_bill
from fintech_agent.rules.wallet_topup_rules import decide_wallet_topup
from fintech_agent.schemas.enums import AuditEventType, CaseStatus
from fintech_agent.schemas.evidence import EvidenceBundle


def apply_rules(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Apply the deterministic rule engine for the selected workflow."""
    workflow = state.get("selected_workflow")
    evidence = state.get("evidence_bundle") or EvidenceBundle()
    case_id = state.get("case_id", "")
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    if workflow == "train_ticket":
        decision = decide_train_ticket(
            ledger=evidence.wallet_ledger,
            provider=evidence.train_provider,
            refund=evidence.refund_status,
            evidence=evidence,
        )
    elif workflow == "utility_bill":
        decision = decide_utility_bill(
            ledger=evidence.wallet_ledger,
            provider=evidence.utility_provider,
            refund=evidence.refund_status,
            evidence=evidence,
        )
    elif workflow == "wallet_topup":
        decision = decide_wallet_topup(
            transaction=evidence.transaction,
            reconciliation=evidence.reconciliation_status,
            evidence=evidence,
        )
    elif workflow == "fraud_account_lock":
        from fintech_agent.rules.fraud_account_lock_rules import decide_fraud_account_lock
        decision = decide_fraud_account_lock(
            account_status=evidence.account_status,
            fraud_case=evidence.fraud_case,
            evidence=evidence,
        )
    elif workflow == "merchant_settlement_delay":
        from fintech_agent.rules.merchant_settlement_rules import decide_merchant_settlement
        decision = decide_merchant_settlement(
            evidence=evidence,
            extracted_info=state.get("extracted_info"),
        )
    else:
        return {
            "status": CaseStatus.MANUAL_REVIEW,
            "errors": [*state.get("errors", []), f"no rule engine for workflow={workflow}"],
            "audit_event_ids": audit_ids,
        }

    if audit:
        ev = audit.log_event(
            case_id, AuditEventType.RULE_APPLIED,
            details={
                "workflow": workflow,
                "action": decision.action.value,
                "diagnosis": decision.diagnosis,
                "approval_required": decision.approval_required,
            },
        )
        audit_ids.append(ev.event_id)

    return {
        "rule_decision": {
            "action": decision.action.value,
            "diagnosis": decision.diagnosis,
            "approval_required": decision.approval_required,
        },
        "approval_required": decision.approval_required,
        "status": CaseStatus.RECOMMENDING,
        "audit_event_ids": audit_ids,
    }
