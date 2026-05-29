"""Node: draft_action — create the actual draft output via MCP client.

Calls the appropriate draft tool based on recommended_action.
All draft actions go through the MCP client adapter, which routes
to the MCP server handlers → safety guards → draft_action_tools.

No direct imports from fintech_agent.tools.draft_action_tools.
"""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.mcp_client.client import get_mcp_client
from fintech_agent.messages.wallet_topup_messages import get_cs_message, get_customer_message
from fintech_agent.schemas.enums import ActionType, AuditEventType, CaseStatus


def create_draft(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Create a draft based on recommended_action.

    All tool calls go through the MCP client adapter.
    """
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

    mcp = get_mcp_client()

    try:
        if action.action_type == ActionType.CREATE_REFUND_REQUEST_DRAFT:
            amount = evidence.wallet_ledger.debit_amount if evidence and evidence.wallet_ledger else 0
            result = mcp.call_tool_sync("create_refund_request_draft", {
                "case_id": case_id,
                "transaction_id": txn_id,
                "user_id": user_id,
                "amount": amount,
                "reason": action.diagnosis,
                "evidence_summary": [action.diagnosis, f"risk={action.risk_level}"],
            })
            if "error" in result:
                raise ValueError(result["error"])

            if audit:
                ev = audit.log_event(
                    case_id, AuditEventType.DRAFT_CREATED,
                    details={
                        "draft_type": "refund_request_draft",
                        "amount": result.get("amount", 0),
                        "draft_id": result.get("draft_id", ""),
                    },
                )
                audit_ids.append(ev.event_id)
            return {
                "draft_output": {
                    "type": "refund_request_draft",
                    "draft_id": result.get("draft_id", ""),
                    "amount": result.get("amount", 0),
                    "status": result.get("status", "created"),
                },
                "status": CaseStatus.DRAFT_CREATED,
                "audit_event_ids": audit_ids,
            }

        elif action.action_type == ActionType.CREATE_RECONCILIATION_TICKET_DRAFT:
            result = mcp.call_tool_sync("create_reconciliation_ticket_draft", {
                "case_id": case_id,
                "transaction_id": txn_id,
                "user_id": user_id,
                "mismatch_type": action.diagnosis,
                "evidence_summary": [action.diagnosis, f"risk={action.risk_level}"],
            })
            if "error" in result:
                raise ValueError(result["error"])

            if audit:
                ev = audit.log_event(
                    case_id, AuditEventType.DRAFT_CREATED,
                    details={
                        "draft_type": "reconciliation_ticket_draft",
                        "draft_id": result.get("draft_id", ""),
                    },
                )
                audit_ids.append(ev.event_id)
            return {
                "draft_output": {
                    "type": "reconciliation_ticket_draft",
                    "draft_id": result.get("draft_id", ""),
                    "status": result.get("status", "created"),
                },
                "status": CaseStatus.DRAFT_CREATED,
                "audit_event_ids": audit_ids,
            }

        elif action.action_type == ActionType.DRAFT_CUSTOMER_RESPONSE:
            # Use mapped customer-friendly message instead of raw diagnosis code
            workflow = state.get("selected_workflow")
            if workflow == "wallet_topup":
                message = get_customer_message(action.diagnosis)
            elif workflow == "fraud_account_lock":
                from fintech_agent.messages.fraud_account_lock_messages import (
                    get_customer_message as get_fraud_customer_message,
                )
                message = get_fraud_customer_message(action.diagnosis)
            else:
                message = f"Kết quả kiểm tra: {action.diagnosis}"
            result = mcp.call_tool_sync("create_customer_response_draft", {
                "case_id": case_id,
                "transaction_id": txn_id,
                "message": message,
            })
            if "error" in result:
                raise ValueError(result["error"])

            if audit:
                ev = audit.log_event(
                    case_id, AuditEventType.DRAFT_CREATED,
                    details={"draft_type": "customer_response_draft"},
                )
                audit_ids.append(ev.event_id)
            return {
                "draft_output": {
                    "type": "customer_response_draft",
                    "draft_id": result.get("draft_id", ""),
                    "status": result.get("status", "created"),
                },
                "status": CaseStatus.DRAFT_CREATED,
                "audit_event_ids": audit_ids,
            }

        elif action.action_type == ActionType.MANUAL_REVIEW:
            workflow = state.get("selected_workflow")
            manual_output: dict = {"type": "manual_review", "status": "pending"}
            if workflow == "wallet_topup":
                manual_output["cs_message"] = get_cs_message(ActionType.MANUAL_REVIEW, action.diagnosis)
            elif workflow == "fraud_account_lock":
                from fintech_agent.messages.fraud_account_lock_messages import (
                    get_cs_message as get_fraud_cs_message,
                )
                manual_output["cs_message"] = get_fraud_cs_message(ActionType.MANUAL_REVIEW, action.diagnosis)
            return {
                "draft_output": manual_output,
                "status": CaseStatus.MANUAL_REVIEW,
                "audit_event_ids": audit_ids,
            }

        elif action.action_type == ActionType.CREATE_UNLOCK_ACCOUNT_DRAFT:
            from fintech_agent.messages.fraud_account_lock_messages import (
                get_cs_message as get_fraud_cs_message,
            )
            result = mcp.call_tool_sync("create_unlock_account_draft", {
                "case_id": case_id,
                "user_id": user_id,
                "reason": get_fraud_cs_message(ActionType.CREATE_UNLOCK_ACCOUNT_DRAFT, action.diagnosis),
                "evidence_summary": [action.diagnosis, f"risk={action.risk_level}"],
            })
            if "error" in result:
                raise ValueError(result["error"])

            if audit:
                ev = audit.log_event(
                    case_id, AuditEventType.DRAFT_CREATED,
                    details={
                        "draft_type": "unlock_account_draft",
                        "draft_id": result.get("draft_id", ""),
                    },
                )
                audit_ids.append(ev.event_id)
            return {
                "draft_output": {
                    "type": "unlock_account_draft",
                    "draft_id": result.get("draft_id", ""),
                    "user_id": result.get("user_id", ""),
                    "status": result.get("status", "pending_approval"),
                    "note": result.get("note", "Draft only. Human approval required."),
                },
                "status": CaseStatus.DRAFT_CREATED,
                "audit_event_ids": audit_ids,
            }

        elif action.action_type == ActionType.CREATE_REQUEST_DOCUMENTS_RESPONSE_DRAFT:
            from fintech_agent.messages.fraud_account_lock_messages import (
                get_cs_message as get_fraud_cs_message,
            )
            result = mcp.call_tool_sync("create_request_documents_response_draft", {
                "case_id": case_id,
                "user_id": user_id,
                "reason": get_fraud_cs_message(
                    ActionType.CREATE_REQUEST_DOCUMENTS_RESPONSE_DRAFT, action.diagnosis,
                ),
                "evidence_summary": [action.diagnosis, f"risk={action.risk_level}"],
            })
            if "error" in result:
                raise ValueError(result["error"])

            if audit:
                ev = audit.log_event(
                    case_id, AuditEventType.DRAFT_CREATED,
                    details={
                        "draft_type": "request_documents_response_draft",
                        "draft_id": result.get("draft_id", ""),
                    },
                )
                audit_ids.append(ev.event_id)
            return {
                "draft_output": {
                    "type": "request_documents_response_draft",
                    "draft_id": result.get("draft_id", ""),
                    "user_id": result.get("user_id", ""),
                    "status": result.get("status", "created"),
                    "message": result.get("message", ""),
                    "note": result.get("note", "Draft only."),
                },
                "status": CaseStatus.DRAFT_CREATED,
                "audit_event_ids": audit_ids,
            }

        elif action.action_type == ActionType.CREATE_FORCE_SUCCESS_DRAFT:
            amount = evidence.transaction.amount if evidence and evidence.transaction else 0
            result = mcp.call_tool_sync("create_force_success_draft", {
                "case_id": case_id,
                "transaction_id": txn_id,
                "user_id": user_id,
                "amount": amount,
                "reason": get_cs_message(ActionType.CREATE_FORCE_SUCCESS_DRAFT, action.diagnosis),
                "evidence_summary": [action.diagnosis, f"risk={action.risk_level}"],
            })
            if "error" in result:
                raise ValueError(result["error"])

            if audit:
                ev = audit.log_event(
                    case_id, AuditEventType.DRAFT_CREATED,
                    details={
                        "draft_type": "force_success_draft",
                        "amount": result.get("amount", 0),
                        "draft_id": result.get("draft_id", ""),
                    },
                )
                audit_ids.append(ev.event_id)
            return {
                "draft_output": {
                    "type": "force_success_draft",
                    "draft_id": result.get("draft_id", ""),
                    "amount": result.get("amount", 0),
                    "status": result.get("status", "created"),
                    "note": result.get("note", "Draft only. Human approval required."),
                },
                "status": CaseStatus.DRAFT_CREATED,
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

    except Exception as e:
        error_msg = str(e)
        # Handle duplicate action errors
        if "duplicate" in error_msg.lower() or "idempotency" in error_msg.lower():
            if audit:
                audit.log_safety_blocked(case_id, action="duplicate_draft", reason=error_msg)
            return {
                "draft_output": {"type": "no_action", "diagnosis": f"duplicate: {e}", "status": "duplicate_blocked"},
                "errors": [*state.get("errors", []), error_msg],
                "status": CaseStatus.CLOSED,
                "audit_event_ids": audit_ids,
            }
        # Handle validation errors
        return {
            "errors": [*state.get("errors", []), error_msg],
            "status": CaseStatus.MANUAL_REVIEW,
            "audit_event_ids": audit_ids,
        }
