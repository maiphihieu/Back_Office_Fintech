"""Node: fetch_evidence — call read-only tools to gather evidence.

Fetches from all data sources and assembles an EvidenceBundle.
Logs tool_called / tool_result_received / tool_failed for each tool.
"""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.schemas.enums import AuditEventType, CaseStatus
from fintech_agent.schemas.evidence import EvidenceBundle
from fintech_agent.tools.tool_errors import ToolDataNotFound, ToolTimeout
from fintech_agent.tools.transaction_tools import get_transaction
from fintech_agent.tools.ledger_tools import get_wallet_ledger
from fintech_agent.tools.train_provider_tools import get_train_provider_status
from fintech_agent.tools.utility_provider_tools import get_utility_bill_status
from fintech_agent.tools.refund_tools import get_refund_status
from fintech_agent.tools.reconciliation_tools import get_reconciliation_status


def _log_tool(audit: AuditLogger | None, case_id: str, tool: str,
              success: bool, summary: str, audit_ids: list[str],
              corr_id: str | None = None) -> None:
    """Helper to log tool_called + tool_result in one go."""
    if not audit:
        return
    ev = audit.log_tool_result(case_id, tool, success=success,
                                result_summary=summary, correlation_id=corr_id)
    audit_ids.append(ev.event_id)


def fetch_evidence(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Fetch all evidence for the extracted transaction."""
    extracted = state.get("extracted_info")
    if not extracted or not extracted.transaction_id:
        return {
            "status": CaseStatus.DEAD_LETTER,
            "errors": [*state.get("errors", []), "no transaction_id for evidence fetch"],
        }

    txn_id = extracted.transaction_id
    case_id = state.get("case_id", "")
    corr_id = state.get("correlation_id")
    errors: list[str] = list(state.get("errors", []))
    tool_results: dict = {}
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    if audit:
        ev = audit.log_event(case_id, AuditEventType.EVIDENCE_FETCH_STARTED,
                             details={"transaction_id": txn_id}, correlation_id=corr_id)
        audit_ids.append(ev.event_id)

    # --- Fetch transaction ---
    transaction = None
    try:
        result = get_transaction(txn_id)
        transaction = result.transaction
        tool_results["transaction"] = "ok"
        _log_tool(audit, case_id, "get_transaction", True, "found", audit_ids, corr_id)
    except (ToolDataNotFound, ToolTimeout) as e:
        errors.append(f"transaction: {e}")
        tool_results["transaction"] = "failed"
        _log_tool(audit, case_id, "get_transaction", False, str(e), audit_ids, corr_id)

    # --- Fetch wallet ledger ---
    wallet_ledger = None
    try:
        result = get_wallet_ledger(txn_id)
        wallet_ledger = result.ledger
        tool_results["wallet_ledger"] = "ok"
        _log_tool(audit, case_id, "get_wallet_ledger", True, "found", audit_ids, corr_id)
    except (ToolDataNotFound, ToolTimeout) as e:
        errors.append(f"wallet_ledger: {e}")
        tool_results["wallet_ledger"] = "failed"
        _log_tool(audit, case_id, "get_wallet_ledger", False, str(e), audit_ids, corr_id)

    # --- Fetch provider status ---
    train_provider = None
    utility_provider = None
    provider_ref_id = transaction.provider_ref_id if transaction else None

    if provider_ref_id:
        svc_type = extracted.service_type or (
            transaction.service_type if transaction else None
        )
        if svc_type == "train_ticket":
            try:
                result = get_train_provider_status(provider_ref_id)
                train_provider = result.provider_status
                tool_results["train_provider"] = "ok"
                _log_tool(audit, case_id, "get_train_provider_status", True, "found", audit_ids, corr_id)
            except (ToolDataNotFound, ToolTimeout) as e:
                errors.append(f"train_provider: {e}")
                tool_results["train_provider"] = "failed"
                _log_tool(audit, case_id, "get_train_provider_status", False, str(e), audit_ids, corr_id)
        elif svc_type in ("utility_bill", "electric_bill", "water_bill"):
            try:
                result = get_utility_bill_status(provider_ref_id)
                utility_provider = result.provider_status
                tool_results["utility_provider"] = "ok"
                _log_tool(audit, case_id, "get_utility_bill_status", True, "found", audit_ids, corr_id)
            except (ToolDataNotFound, ToolTimeout) as e:
                errors.append(f"utility_provider: {e}")
                tool_results["utility_provider"] = "failed"
                _log_tool(audit, case_id, "get_utility_bill_status", False, str(e), audit_ids, corr_id)

    # --- Fetch refund status ---
    refund_status = None
    try:
        result = get_refund_status(txn_id)
        refund_status = result.refund_status
        tool_results["refund_status"] = "ok"
        _log_tool(audit, case_id, "get_refund_status", True, "found", audit_ids, corr_id)
    except (ToolDataNotFound, ToolTimeout) as e:
        errors.append(f"refund_status: {e}")
        tool_results["refund_status"] = "failed"
        _log_tool(audit, case_id, "get_refund_status", False, str(e), audit_ids, corr_id)

    # --- Fetch reconciliation ---
    reconciliation = None
    result = get_reconciliation_status(txn_id)
    reconciliation = result.reconciliation
    tool_results["reconciliation"] = "ok"

    # --- Assemble evidence bundle ---
    evidence = EvidenceBundle(
        transaction=transaction,
        wallet_ledger=wallet_ledger,
        train_provider=train_provider,
        utility_provider=utility_provider,
        refund_status=refund_status,
        reconciliation=reconciliation,
    )

    # Determine workflow from transaction data
    selected_workflow = state.get("selected_workflow")
    if not selected_workflow and transaction:
        if transaction.service_type == "train_ticket":
            selected_workflow = "train_ticket"
        elif transaction.service_type in ("electric_bill", "water_bill"):
            selected_workflow = "utility_bill"

    return {
        "evidence_bundle": evidence,
        "tool_results": tool_results,
        "selected_workflow": selected_workflow,
        "errors": errors,
        "status": CaseStatus.FETCHING_EVIDENCE,
        "audit_event_ids": audit_ids,
    }
