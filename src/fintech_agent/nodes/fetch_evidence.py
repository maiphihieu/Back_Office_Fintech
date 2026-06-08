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
    BankTransferReceipt,
    EvidenceBundle,
    FraudCase,
    MerchantBankAccount,
    MerchantPayout,
    MerchantProfile,
    MerchantSettlementLedger,
    ReconciliationStatus,
    RefundStatus,
    SettlementBatch,
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

    # ── Merchant settlement workflow — merchant data sources ──
    if svc_type == "merchant_settlement":
        return _fetch_merchant_settlement_evidence(
            state, extracted, mcp, audit, case_id, corr_id, errors, audit_ids,
        )

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

    Identity resolution priority:
    1. Explicit user_id (from complaint text or pre-supplied)
    2. Phone number lookup (via get_user_by_phone MCP tool)
    3. Email lookup (via get_user_by_email MCP tool)
    4. Wallet_id lookup (via get_user_by_wallet_id MCP tool)

    If identity cannot be resolved, returns evidence with missing_identity
    marker. The rule engine will then route to manual_review.

    SAFETY: Does NOT default to U_FRAUD_001 or any other hardcoded user_id.
    """
    user_id = (
        (extracted.user_id if extracted else None)
        or state.get("user_id")
    )
    tool_results: dict = {}
    identity_source: str | None = None

    # ── Identity resolution via phone/email/wallet_id ─────────
    if not user_id and extracted:
        # Try phone lookup
        phone = getattr(extracted, "phone", None)
        if phone:
            try:
                result = mcp.call_tool_sync("get_user_by_phone", {"phone": phone})
                if "error" not in result:
                    user_id = result.get("user_id")
                    identity_source = f"phone:{phone}"
                    tool_results["identity_lookup"] = "ok_phone"
                    _log_tool(audit, case_id, "get_user_by_phone", True,
                              f"resolved={user_id}", audit_ids, corr_id)
                else:
                    tool_results["identity_lookup_phone"] = "not_found"
                    _log_tool(audit, case_id, "get_user_by_phone", False,
                              result["error"], audit_ids, corr_id)
            except Exception as e:
                tool_results["identity_lookup_phone"] = "failed"
                _log_tool(audit, case_id, "get_user_by_phone", False,
                          str(e), audit_ids, corr_id)

        # Try email lookup
        if not user_id:
            email = getattr(extracted, "email", None)
            if email:
                try:
                    result = mcp.call_tool_sync("get_user_by_email", {"email": email})
                    if "error" not in result:
                        user_id = result.get("user_id")
                        identity_source = f"email:{email}"
                        tool_results["identity_lookup"] = "ok_email"
                        _log_tool(audit, case_id, "get_user_by_email", True,
                                  f"resolved={user_id}", audit_ids, corr_id)
                    else:
                        tool_results["identity_lookup_email"] = "not_found"
                        _log_tool(audit, case_id, "get_user_by_email", False,
                                  result["error"], audit_ids, corr_id)
                except Exception as e:
                    tool_results["identity_lookup_email"] = "failed"
                    _log_tool(audit, case_id, "get_user_by_email", False,
                              str(e), audit_ids, corr_id)

        # Try wallet_id lookup
        if not user_id:
            wallet_id = getattr(extracted, "wallet_id", None)
            if wallet_id:
                try:
                    result = mcp.call_tool_sync("get_user_by_wallet_id",
                                                {"wallet_id": wallet_id})
                    if "error" not in result:
                        user_id = result.get("user_id")
                        identity_source = f"wallet_id:{wallet_id}"
                        tool_results["identity_lookup"] = "ok_wallet_id"
                        _log_tool(audit, case_id, "get_user_by_wallet_id", True,
                                  f"resolved={user_id}", audit_ids, corr_id)
                    else:
                        tool_results["identity_lookup_wallet_id"] = "not_found"
                        _log_tool(audit, case_id, "get_user_by_wallet_id", False,
                                  result["error"], audit_ids, corr_id)
                except Exception as e:
                    tool_results["identity_lookup_wallet_id"] = "failed"
                    _log_tool(audit, case_id, "get_user_by_wallet_id", False,
                              str(e), audit_ids, corr_id)

    # ── Identity not resolved — cannot proceed safely ─────────
    if not user_id:
        errors.append("identity_not_resolved: no user_id, phone, email, or wallet_id")
        tool_results["identity_lookup"] = "missing"

        if audit:
            ev = audit.log_event(
                case_id, AuditEventType.EVIDENCE_FETCH_STARTED,
                details={"user_id": None, "workflow": "fraud_account_lock",
                         "identity_status": "missing"},
                correlation_id=corr_id,
            )
            audit_ids.append(ev.event_id)

        # Return empty evidence — rule engine will route to manual_review
        return {
            "evidence_bundle": EvidenceBundle(),
            "tool_results": tool_results,
            "selected_workflow": "fraud_account_lock",
            "errors": errors,
            "status": CaseStatus.FETCHING_EVIDENCE,
            "audit_event_ids": audit_ids,
        }

    # ── Identity resolved — fetch evidence ────────────────────
    if identity_source:
        tool_results["identity_source"] = identity_source

    if audit:
        ev = audit.log_event(
            case_id, AuditEventType.EVIDENCE_FETCH_STARTED,
            details={"user_id": user_id, "workflow": "fraud_account_lock",
                     "identity_source": identity_source or "direct"},
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
        "user_id": user_id,
        "selected_workflow": "fraud_account_lock",
        "errors": errors,
        "status": CaseStatus.FETCHING_EVIDENCE,
        "audit_event_ids": audit_ids,
    }


def _fetch_merchant_settlement_evidence(
    state: AgentState,
    extracted,
    mcp,
    audit: AuditLogger | None,
    case_id: str,
    corr_id: str | None,
    errors: list[str],
    audit_ids: list[str],
) -> AgentState:
    """Fetch merchant settlement evidence via MCP tools.

    Identity resolution priority:
    1. Explicit merchant_id from complaint
    2. Phone number lookup
    3. Email lookup
    4. Tax code lookup

    If merchant not found, returns empty bundle (rule engine -> manual_review).
    SAFETY: Does NOT use fake/default merchant.
    """
    tool_results: dict = {}
    merchant_id: str | None = getattr(extracted, "merchant_id", None) if extracted else None

    if audit:
        ev = audit.log_event(
            case_id, AuditEventType.EVIDENCE_FETCH_STARTED,
            details={"workflow": "merchant_settlement_delay", "merchant_id": merchant_id},
            correlation_id=corr_id,
        )
        audit_ids.append(ev.event_id)

    # ── Step 1: Resolve merchant profile ──────────────────
    merchant_profile: MerchantProfile | None = None
    lookup_args: dict = {}

    if merchant_id:
        lookup_args = {"merchant_id": merchant_id}
    elif extracted:
        phone = getattr(extracted, "phone", None)
        email = getattr(extracted, "email", None)
        tax_code = getattr(extracted, "tax_code", None)
        if phone:
            lookup_args = {"phone": phone}
        elif email:
            lookup_args = {"email": email}
        elif tax_code:
            lookup_args = {"tax_code": tax_code}

    if lookup_args:
        try:
            result = mcp.call_tool_sync("get_merchant_profile", lookup_args)
            merchant_profile = _parse_or_none(MerchantProfile, result)
            if merchant_profile:
                merchant_id = merchant_profile.merchant_id
                tool_results["merchant_profile"] = "ok"
                _log_tool(audit, case_id, "get_merchant_profile", True,
                          f"found={merchant_id}", audit_ids, corr_id)
            else:
                tool_results["merchant_profile"] = "not_found"
                _log_tool(audit, case_id, "get_merchant_profile", False,
                          "not found", audit_ids, corr_id)
        except Exception as e:
            errors.append(f"merchant_profile: {e}")
            tool_results["merchant_profile"] = "failed"
            _log_tool(audit, case_id, "get_merchant_profile", False,
                      str(e), audit_ids, corr_id)
    else:
        tool_results["merchant_profile"] = "no_identity"
        errors.append("merchant_identity_not_provided")

    # If merchant not found, return empty bundle — rule engine will handle
    if not merchant_id:
        return {
            "evidence_bundle": EvidenceBundle(),
            "tool_results": tool_results,
            "selected_workflow": "merchant_settlement_delay",
            "errors": errors,
            "status": CaseStatus.FETCHING_EVIDENCE,
            "audit_event_ids": audit_ids,
        }

    # ── Step 2: Fetch merchant bank account ───────────────
    bank_account: MerchantBankAccount | None = None
    try:
        result = mcp.call_tool_sync("get_merchant_bank_account", {"merchant_id": merchant_id})
        bank_account = _parse_or_none(MerchantBankAccount, result)
        tool_results["merchant_bank_account"] = "ok" if bank_account else "not_found"
        _log_tool(audit, case_id, "get_merchant_bank_account",
                  bank_account is not None, "found" if bank_account else "not_found",
                  audit_ids, corr_id)
    except Exception as e:
        errors.append(f"merchant_bank_account: {e}")
        tool_results["merchant_bank_account"] = "failed"
        _log_tool(audit, case_id, "get_merchant_bank_account", False, str(e), audit_ids, corr_id)

    # ── Step 3: Fetch settlement ledger ───────────────────
    ledger: MerchantSettlementLedger | None = None
    ledger_args: dict = {"merchant_id": merchant_id}
    settlement_date = getattr(extracted, "settlement_date", None) if extracted else None
    if settlement_date:
        ledger_args["settlement_date"] = settlement_date
    try:
        result = mcp.call_tool_sync("get_merchant_settlement_ledger", ledger_args)
        ledger = _parse_or_none(MerchantSettlementLedger, result)
        tool_results["merchant_settlement_ledger"] = "ok" if ledger else "not_found"
        _log_tool(audit, case_id, "get_merchant_settlement_ledger",
                  ledger is not None, "found" if ledger else "not_found",
                  audit_ids, corr_id)
    except Exception as e:
        errors.append(f"merchant_settlement_ledger: {e}")
        tool_results["merchant_settlement_ledger"] = "failed"
        _log_tool(audit, case_id, "get_merchant_settlement_ledger", False, str(e), audit_ids, corr_id)

    # ── Step 4: Fetch merchant payout ─────────────────────
    payout: MerchantPayout | None = None
    payout_args: dict = {"merchant_id": merchant_id}
    payout_id = getattr(extracted, "payout_id", None) if extracted else None
    if payout_id:
        payout_args["payout_id"] = payout_id
    elif settlement_date:
        payout_args["settlement_date"] = settlement_date
    try:
        result = mcp.call_tool_sync("get_merchant_payout", payout_args)
        payout = _parse_or_none(MerchantPayout, result)
        tool_results["merchant_payout"] = "ok" if payout else "not_found"
        _log_tool(audit, case_id, "get_merchant_payout",
                  payout is not None, "found" if payout else "not_found",
                  audit_ids, corr_id)
    except Exception as e:
        errors.append(f"merchant_payout: {e}")
        tool_results["merchant_payout"] = "failed"
        _log_tool(audit, case_id, "get_merchant_payout", False, str(e), audit_ids, corr_id)

    # ── Step 5: Fetch settlement batch ───────────────────
    batch: SettlementBatch | None = None
    batch_args: dict = {}
    batch_id = getattr(extracted, "batch_id", None) if extracted else None
    if batch_id:
        batch_args = {"batch_id": batch_id}
    elif settlement_date:
        settlement_cycle = getattr(extracted, "settlement_cycle", "D+1") if extracted else "D+1"
        batch_args = {"settlement_date": settlement_date, "cycle": settlement_cycle or "D+1"}
    elif ledger and ledger.settlement_date:
        # Use ledger's settlement date to find the batch
        settlement_cycle = getattr(extracted, "settlement_cycle", "D+1") if extracted else "D+1"
        batch_args = {"settlement_date": ledger.settlement_date, "cycle": settlement_cycle or "D+1"}

    if batch_args:
        try:
            result = mcp.call_tool_sync("get_settlement_batch", batch_args)
            batch = _parse_or_none(SettlementBatch, result)
            tool_results["settlement_batch"] = "ok" if batch else "not_found"
            _log_tool(audit, case_id, "get_settlement_batch",
                      batch is not None, "found" if batch else "not_found",
                      audit_ids, corr_id)
        except Exception as e:
            errors.append(f"settlement_batch: {e}")
            tool_results["settlement_batch"] = "failed"
            _log_tool(audit, case_id, "get_settlement_batch", False, str(e), audit_ids, corr_id)

    # ── Step 6: Fetch bank transfer receipt ──────────────
    receipt: BankTransferReceipt | None = None
    receipt_args: dict = {}
    if payout and payout.bank_transfer_ref:
        receipt_args = {"bank_transfer_ref": payout.bank_transfer_ref}
    elif payout:
        receipt_args = {"payout_id": payout.payout_id}

    if receipt_args:
        try:
            result = mcp.call_tool_sync("get_bank_transfer_receipt", receipt_args)
            receipt = _parse_or_none(BankTransferReceipt, result)
            tool_results["bank_transfer_receipt"] = "ok" if receipt else "not_found"
            _log_tool(audit, case_id, "get_bank_transfer_receipt",
                      receipt is not None, "found" if receipt else "not_found",
                      audit_ids, corr_id)
        except Exception as e:
            errors.append(f"bank_transfer_receipt: {e}")
            tool_results["bank_transfer_receipt"] = "failed"
            _log_tool(audit, case_id, "get_bank_transfer_receipt", False, str(e), audit_ids, corr_id)

    # ── Assemble evidence bundle ───────────────────────
    evidence = EvidenceBundle(
        merchant_profile=merchant_profile,
        merchant_bank_account=bank_account,
        merchant_settlement_ledger=ledger,
        merchant_payout=payout,
        settlement_batch=batch,
        bank_transfer_receipt=receipt,
    )

    return {
        "evidence_bundle": evidence,
        "tool_results": tool_results,
        "selected_workflow": "merchant_settlement_delay",
        "errors": errors,
        "status": CaseStatus.FETCHING_EVIDENCE,
        "audit_event_ids": audit_ids,
    }

