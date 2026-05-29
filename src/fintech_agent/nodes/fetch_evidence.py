"""Node: fetch_evidence — call read-only tools via MCP client to gather evidence.

Fetches from all data sources and assembles an EvidenceBundle.
Logs tool_called / tool_result_received / tool_failed for each tool.

All tool calls go through the MCP client adapter, which routes to
the MCP server handlers → repository layer. No direct tool imports.
"""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.mcp_client.client import get_mcp_client
from fintech_agent.schemas.enums import AuditEventType, CaseStatus
from fintech_agent.schemas.evidence import (
    AccountStatus,
    EvidenceBundle,
    FraudCase,
    ReconciliationStatus,
    RefundStatus,
    TrainProviderStatus,
    Transaction,
    UtilityProviderStatus,
    WalletLedger,
)


def _log_tool(audit: AuditLogger | None, case_id: str, tool: str,
              success: bool, summary: str, audit_ids: list[str],
              corr_id: str | None = None) -> None:
    """Helper to log tool_called + tool_result in one go."""
    if not audit:
        return
    ev = audit.log_tool_result(case_id, tool, success=success,
                                result_summary=summary, correlation_id=corr_id)
    audit_ids.append(ev.event_id)


def _parse_or_none(model_cls, data: dict):
    """Parse a dict into a Pydantic model, return None if data has 'error' key."""
    if not data or "error" in data:
        return None
    try:
        return model_cls.model_validate(data)
    except Exception:
        return None


def fetch_evidence(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Fetch all evidence for the extracted transaction.

    All tool calls go through the MCP client adapter.
    """
    extracted = state.get("extracted_info")
    case_id = state.get("case_id", "")
    corr_id = state.get("correlation_id")
    errors: list[str] = list(state.get("errors", []))
    audit_ids: list[str] = list(state.get("audit_event_ids", []))
    mcp = get_mcp_client()

    # ── Fraud/account lock workflow — different data sources ──
    svc_type = extracted.service_type if extracted else None
    if svc_type == "account_security":
        return _fetch_fraud_evidence(state, extracted, mcp, audit, case_id, corr_id, errors, audit_ids)

    # ── Transaction-based workflows ──────────────────────────
    if not extracted or not extracted.transaction_id:
        return {
            "status": CaseStatus.DEAD_LETTER,
            "errors": [*errors, "no transaction_id for evidence fetch"],
        }

    txn_id = extracted.transaction_id
    case_id = state.get("case_id", "")
    corr_id = state.get("correlation_id")
    errors: list[str] = list(state.get("errors", []))
    tool_results: dict = {}
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    mcp = get_mcp_client()

    if audit:
        ev = audit.log_event(case_id, AuditEventType.EVIDENCE_FETCH_STARTED,
                             details={"transaction_id": txn_id}, correlation_id=corr_id)
        audit_ids.append(ev.event_id)

    # --- Fetch transaction via MCP ---
    transaction = None
    try:
        result = mcp.call_tool_sync("get_transaction", {"transaction_id": txn_id})
        if "error" in result:
            raise ValueError(result["error"])
        transaction = Transaction.model_validate(result)
        tool_results["transaction"] = "ok"
        _log_tool(audit, case_id, "get_transaction", True, "found", audit_ids, corr_id)
    except Exception as e:
        errors.append(f"transaction: {e}")
        tool_results["transaction"] = "failed"
        _log_tool(audit, case_id, "get_transaction", False, str(e), audit_ids, corr_id)

    # --- Fetch wallet ledger via MCP ---
    wallet_ledger = None
    # wallet_topup doesn't require wallet_ledger
    svc_hint = extracted.service_type or (
        transaction.service_type if transaction else None
    )
    skip_wallet_tools = svc_hint == "wallet_topup"

    if not skip_wallet_tools:
        try:
            result = mcp.call_tool_sync("get_wallet_ledger", {"transaction_id": txn_id})
            if "error" in result:
                raise ValueError(result["error"])
            wallet_ledger = WalletLedger.model_validate(result)
            tool_results["wallet_ledger"] = "ok"
            _log_tool(audit, case_id, "get_wallet_ledger", True, "found", audit_ids, corr_id)
        except Exception as e:
            errors.append(f"wallet_ledger: {e}")
            tool_results["wallet_ledger"] = "failed"
            _log_tool(audit, case_id, "get_wallet_ledger", False, str(e), audit_ids, corr_id)

    # --- Fetch provider status via MCP ---
    train_provider = None
    utility_provider = None
    provider_ref_id = transaction.provider_ref_id if transaction else None

    if provider_ref_id and not skip_wallet_tools:
        if svc_hint == "train_ticket":
            try:
                result = mcp.call_tool_sync(
                    "get_train_provider_status", {"provider_ref_id": provider_ref_id}
                )
                if "error" in result:
                    raise ValueError(result["error"])
                train_provider = TrainProviderStatus.model_validate(result)
                tool_results["train_provider"] = "ok"
                _log_tool(audit, case_id, "get_train_provider_status", True, "found", audit_ids, corr_id)
            except Exception as e:
                errors.append(f"train_provider: {e}")
                tool_results["train_provider"] = "failed"
                _log_tool(audit, case_id, "get_train_provider_status", False, str(e), audit_ids, corr_id)
        elif svc_hint in ("utility_bill", "electric_bill", "water_bill"):
            try:
                result = mcp.call_tool_sync(
                    "get_utility_bill_status", {"provider_ref_id": provider_ref_id}
                )
                if "error" in result:
                    raise ValueError(result["error"])
                utility_provider = UtilityProviderStatus.model_validate(result)
                tool_results["utility_provider"] = "ok"
                _log_tool(audit, case_id, "get_utility_bill_status", True, "found", audit_ids, corr_id)
            except Exception as e:
                errors.append(f"utility_provider: {e}")
                tool_results["utility_provider"] = "failed"
                _log_tool(audit, case_id, "get_utility_bill_status", False, str(e), audit_ids, corr_id)

    # --- Fetch refund status via MCP ---
    refund_status = None
    if not skip_wallet_tools:
        try:
            result = mcp.call_tool_sync("get_refund_status", {"transaction_id": txn_id})
            if "error" in result:
                raise ValueError(result["error"])
            refund_status = RefundStatus.model_validate(result)
            tool_results["refund_status"] = "ok"
            _log_tool(audit, case_id, "get_refund_status", True, "found", audit_ids, corr_id)
        except Exception as e:
            errors.append(f"refund_status: {e}")
            tool_results["refund_status"] = "failed"
            _log_tool(audit, case_id, "get_refund_status", False, str(e), audit_ids, corr_id)

    # --- Fetch reconciliation via MCP ---
    reconciliation = None
    try:
        result = mcp.call_tool_sync("get_reconciliation_status", {"transaction_id": txn_id})
        if "error" not in result:
            reconciliation = ReconciliationStatus.model_validate(result)
        tool_results["reconciliation"] = "ok"
    except Exception as e:
        errors.append(f"reconciliation: {e}")
        tool_results["reconciliation"] = "failed"

    # --- Assemble evidence bundle ---
    evidence = EvidenceBundle(
        transaction=transaction,
        wallet_ledger=wallet_ledger,
        train_provider=train_provider,
        utility_provider=utility_provider,
        refund_status=refund_status,
        reconciliation_status=reconciliation,
    )

    # Determine workflow from transaction data
    selected_workflow = state.get("selected_workflow")
    if not selected_workflow and transaction:
        if transaction.service_type == "train_ticket":
            selected_workflow = "train_ticket"
        elif transaction.service_type in ("electric_bill", "water_bill"):
            selected_workflow = "utility_bill"
        elif transaction.service_type == "wallet_topup":
            selected_workflow = "wallet_topup"

    return {
        "evidence_bundle": evidence,
        "tool_results": tool_results,
        "selected_workflow": selected_workflow,
        "errors": errors,
        "status": CaseStatus.FETCHING_EVIDENCE,
        "audit_event_ids": audit_ids,
    }


def _fetch_fraud_evidence(
    state: AgentState,
    extracted,
    mcp,
    audit: AuditLogger | None,
    case_id: str,
    corr_id: str | None,
    errors: list[str],
    audit_ids: list[str],
) -> AgentState:
    """Fetch account status and fraud case for fraud_account_lock workflow.

    This workflow does NOT need transaction_id, wallet_ledger, provider,
    refund, or reconciliation. It only needs account + fraud_case.
    """
    user_id = (
        (extracted.user_id if extracted else None)
        or state.get("user_id")
        or "U_FRAUD_001"
    )
    tool_results: dict = {}

    if audit:
        ev = audit.log_event(
            case_id, AuditEventType.EVIDENCE_FETCH_STARTED,
            details={"user_id": user_id, "workflow": "fraud_account_lock"},
            correlation_id=corr_id,
        )
        audit_ids.append(ev.event_id)

    # Fetch account status via MCP
    account_status = None
    try:
        result = mcp.call_tool_sync("get_account_status", {"user_id": user_id})
        if "error" in result:
            raise ValueError(result["error"])
        account_status = AccountStatus.model_validate(result)
        tool_results["account_status"] = "ok"
        _log_tool(audit, case_id, "get_account_status", True, "found", audit_ids, corr_id)
    except Exception as e:
        errors.append(f"account_status: {e}")
        tool_results["account_status"] = "failed"
        _log_tool(audit, case_id, "get_account_status", False, str(e), audit_ids, corr_id)

    # Fetch fraud case via MCP
    fraud_case = None
    try:
        result = mcp.call_tool_sync("get_fraud_case", {"user_id": user_id})
        if "error" in result:
            raise ValueError(result["error"])
        fraud_case = FraudCase.model_validate(result)
        tool_results["fraud_case"] = "ok"
        _log_tool(audit, case_id, "get_fraud_case", True, "found", audit_ids, corr_id)
    except Exception as e:
        errors.append(f"fraud_case: {e}")
        tool_results["fraud_case"] = "failed"
        _log_tool(audit, case_id, "get_fraud_case", False, str(e), audit_ids, corr_id)

    evidence = EvidenceBundle(
        account_status=account_status,
        fraud_case=fraud_case,
    )

    return {
        "evidence_bundle": evidence,
        "tool_results": tool_results,
        "selected_workflow": "fraud_account_lock",
        "errors": errors,
        "status": CaseStatus.FETCHING_EVIDENCE,
        "audit_event_ids": audit_ids,
    }

