"""Node: draft_action — create the actual draft output.

Calls the appropriate draft tool based on recommended_action.
All money-related drafts go through safety guards.
"""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.schemas.enums import ActionType, AuditEventType, CaseStatus
from fintech_agent.tools.draft_action_tools import (
    DraftStore,
    create_customer_response_draft,
    create_reconciliation_ticket_draft,
    create_refund_request_draft,
)
from fintech_agent.tools.tool_errors import DuplicateActionError, ToolValidationError


def create_draft(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Create a draft based on recommended_action."""
    action = state.get("recommended_action")
    case_id = state.get("case_id", "UNKNOWN")
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    if not action:
        return {
            "status": CaseStatus.MANUAL_REVIEW,
            "errors": [*state.get("errors", []), "no recommended_action for draft"],
            "audit_event_ids": audit_ids,
        }

    evidence = state.get("evidence_bundle")
    extracted = state.get("extracted_info")
    user_id = state.get("user_id") or "UNKNOWN"
    txn_id = (extracted.transaction_id if extracted and extracted.transaction_id else None) or "UNKNOWN"
    store = DraftStore()  # Fresh store per run (MVP)

    try:
        if action.action_type == ActionType.CREATE_REFUND_REQUEST_DRAFT:
            amount = evidence.wallet_ledger.debit_amount if evidence and evidence.wallet_ledger else 0
            result = create_refund_request_draft(
                case_id=case_id,
                transaction_id=txn_id,
                user_id=user_id,
                amount=amount,
                reason=action.diagnosis,
                evidence_summary=[action.diagnosis, f"risk={action.risk_level}"],
                refund_status=evidence.refund_status if evidence else None,
                store=store,
            )
            if audit:
                ev = audit.log_event(
                    case_id, AuditEventType.DRAFT_CREATED,
                    details={
                        "draft_type": "refund_request_draft",
                        "amount": result.draft.amount if result.draft else 0,
                        "idempotency_key": result.idempotency_key,
                    },
                )
                audit_ids.append(ev.event_id)
            return {
                "draft_output": {
                    "type": "refund_request_draft",
                    "idempotency_key": result.idempotency_key,
                    "amount": result.draft.amount if result.draft else 0,
                    "status": "created",
                },
                "status": CaseStatus.DRAFT_CREATED,
                "audit_event_ids": audit_ids,
            }

        elif action.action_type == ActionType.CREATE_RECONCILIATION_TICKET_DRAFT:
            result = create_reconciliation_ticket_draft(
                case_id=case_id,
                transaction_id=txn_id,
                user_id=user_id,
                mismatch_type=action.diagnosis,
                evidence_summary=[action.diagnosis, f"risk={action.risk_level}"],
                store=store,
            )
            if audit:
                ev = audit.log_event(
                    case_id, AuditEventType.DRAFT_CREATED,
                    details={
                        "draft_type": "reconciliation_ticket_draft",
                        "idempotency_key": result.idempotency_key,
                    },
                )
                audit_ids.append(ev.event_id)
            return {
                "draft_output": {
                    "type": "reconciliation_ticket_draft",
                    "idempotency_key": result.idempotency_key,
                    "status": "created",
                },
                "status": CaseStatus.DRAFT_CREATED,
                "audit_event_ids": audit_ids,
            }

        elif action.action_type == ActionType.DRAFT_CUSTOMER_RESPONSE:
            result = create_customer_response_draft(
                case_id=case_id,
                transaction_id=txn_id,
                message=f"Kết quả kiểm tra: {action.diagnosis}",
                store=store,
            )
            if audit:
                ev = audit.log_event(
                    case_id, AuditEventType.DRAFT_CREATED,
                    details={"draft_type": "customer_response_draft"},
                )
                audit_ids.append(ev.event_id)
            return {
                "draft_output": {
                    "type": "customer_response_draft",
                    "message": result.draft.message if result.draft else "",
                    "status": "created",
                },
                "status": CaseStatus.DRAFT_CREATED,
                "audit_event_ids": audit_ids,
            }

        elif action.action_type == ActionType.MANUAL_REVIEW:
            return {
                "draft_output": {"type": "manual_review", "status": "pending"},
                "status": CaseStatus.MANUAL_REVIEW,
                "audit_event_ids": audit_ids,
            }

        elif action.action_type == ActionType.NO_ACTION:
            if audit:
                ev = audit.log_event(
                    case_id, AuditEventType.DRAFT_CREATED,
                    details={"draft_type": "no_action", "diagnosis": action.diagnosis},
                )
                audit_ids.append(ev.event_id)
            return {
                "draft_output": {"type": "no_action", "diagnosis": action.diagnosis, "status": "closed"},
                "status": CaseStatus.CLOSED,
                "audit_event_ids": audit_ids,
            }

        else:
            # WAIT_SLA
            return {
                "draft_output": {"type": action.action_type.value, "status": "no_draft_needed"},
                "status": CaseStatus.CLOSED,
                "audit_event_ids": audit_ids,
            }

    except DuplicateActionError as e:
        if audit:
            audit.log_safety_blocked(case_id, action="duplicate_draft", reason=str(e))
        return {
            "draft_output": {"type": "no_action", "diagnosis": f"duplicate: {e}", "status": "duplicate_blocked"},
            "errors": [*state.get("errors", []), str(e)],
            "status": CaseStatus.CLOSED,
            "audit_event_ids": audit_ids,
        }
    except ToolValidationError as e:
        return {
            "errors": [*state.get("errors", []), str(e)],
            "status": CaseStatus.MANUAL_REVIEW,
            "audit_event_ids": audit_ids,
        }
