"""Node: missing_info — handle cases with incomplete extraction."""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.schemas.enums import AuditEventType, CaseStatus

# Workflows that REQUIRE transaction_id to proceed
_TRANSACTION_WORKFLOWS = frozenset({
    "train_ticket", "utility_bill", "wallet_topup",
    "train_ticket_reconciliation", "utility_bill_reconciliation",
})


def missing_info_handler(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Handle missing information.

    Workflow-aware logic:
    - Transaction-based workflows: transaction_id missing → dead_letter.
    - fraud_account_lock: transaction_id is NOT required.
      Missing identity (user_id without phone/email/wallet_id) → dead_letter
      with a fraud-specific message asking for identity info.
    - Other missing fields → proceed with what we have.
    """
    missing = state.get("missing_info", [])
    case_id = state.get("case_id", "")
    selected_workflow = state.get("selected_workflow")
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    is_fraud_workflow = selected_workflow == "fraud_account_lock"

    # For transaction-based workflows, missing transaction_id is critical
    if "transaction_id" in missing and not is_fraud_workflow:
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

    # For fraud workflow, missing identity (user_id without phone/email/wallet_id) is critical
    if is_fraud_workflow and "user_id" in missing:
        if audit:
            ev = audit.log_event(
                case_id, AuditEventType.DEAD_LETTER_CREATED,
                actor="system",
                details={
                    "reason": "identity_missing_for_fraud",
                    "missing": missing,
                    "workflow": "fraud_account_lock",
                },
                new_status=CaseStatus.DEAD_LETTER.value,
            )
            audit_ids.append(ev.event_id)
        return {
            "status": CaseStatus.DEAD_LETTER,
            "errors": [
                *state.get("errors", []),
                (
                    "critical: identity_missing — "
                    "Chưa đủ thông tin định danh tài khoản. "
                    "Vui lòng cung cấp số điện thoại/email/user_id/wallet_id "
                    "đăng ký ví để agent tra cứu trạng thái khóa và dữ liệu Risk/Fraud."
                ),
            ],
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

