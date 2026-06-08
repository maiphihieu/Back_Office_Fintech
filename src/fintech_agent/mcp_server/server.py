"""Fintech Back-office MCP Server — real MCP server using Python MCP SDK.

Server name: fintech-backoffice-mcp-server

Exposes 23 tools:
  Read-only (17):  get_transaction, get_reconciliation_status, get_wallet_ledger,
                   get_refund_status, get_train_provider_status, get_utility_bill_status,
                   get_account_status, get_fraud_case,
                   get_user_by_phone, get_user_by_email, get_user_by_wallet_id,
                   get_merchant_profile, get_merchant_bank_account,
                   get_settlement_batch, get_merchant_settlement_ledger,
                   get_merchant_payout, get_bank_transfer_receipt
  Draft-only (6):  create_refund_request_draft, create_reconciliation_ticket_draft,
                   create_customer_response_draft, create_force_success_draft,
                   create_unlock_account_draft, create_request_documents_response_draft

Draft tools do NOT execute financial operations. They create draft objects
that require human approval before any money-impacting action.
"""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP

from fintech_agent.mcp_server.handlers import (
    handle_create_customer_response_draft,
    handle_create_force_success_draft,
    handle_create_reconciliation_ticket_draft,
    handle_create_refund_request_draft,
    handle_create_request_documents_response_draft,
    handle_create_unlock_account_draft,
    handle_get_account_status,
    handle_get_bank_transfer_receipt,
    handle_get_fraud_case,
    handle_get_merchant_bank_account,
    handle_get_merchant_payout,
    handle_get_merchant_profile,
    handle_get_merchant_settlement_ledger,
    handle_get_reconciliation_status,
    handle_get_refund_status,
    handle_get_settlement_batch,
    handle_get_train_provider_status,
    handle_get_transaction,
    handle_get_user_by_email,
    handle_get_user_by_phone,
    handle_get_user_by_wallet_id,
    handle_get_utility_bill_status,
    handle_get_wallet_ledger,
)

_logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#  Create the MCP server instance
# ═══════════════════════════════════════════════════════════════

mcp = FastMCP(
    "fintech-backoffice-mcp-server",
    instructions=(
        "Fintech back-office MCP server for AI agent workflows. "
        "Provides read-only tools to query transactions, reconciliation, "
        "wallet ledger, refund, and provider status. "
        "Also provides draft-only tools that create pending drafts — "
        "no financial operations are executed. "
        "All money-impacting actions require human approval."
    ),
)


# ═══════════════════════════════════════════════════════════════
#  Read-only tools
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def get_transaction(transaction_id: str) -> str:
    """Fetch transaction details by transaction ID.

    Returns transaction data including status, amount, service_type,
    user_id, and provider_ref_id. Read-only — does not modify any data.

    Args:
        transaction_id: The unique transaction identifier (e.g. TXN_TOPUP_001)
    """
    result = await handle_get_transaction(transaction_id)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def get_reconciliation_status(transaction_id: str) -> str:
    """Fetch bank reconciliation status for a transaction.

    Returns reconciliation data including bank_status, bank_amount,
    money_received_in_master_wallet, and mismatch_type. Read-only.

    Args:
        transaction_id: The transaction identifier to look up reconciliation for
    """
    result = await handle_get_reconciliation_status(transaction_id)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def get_wallet_ledger(transaction_id: str) -> str:
    """Fetch wallet ledger entry for a transaction.

    Returns ledger data including debit_amount, balance_before, balance_after.
    This is the source of truth for money. Read-only.

    Args:
        transaction_id: The transaction identifier to look up ledger for
    """
    result = await handle_get_wallet_ledger(transaction_id)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def get_refund_status(transaction_id: str) -> str:
    """Fetch refund status for a transaction.

    Returns refund data including refund_id, status, refund_amount.
    This is the source of truth for refund lifecycle. Read-only.

    Args:
        transaction_id: The transaction identifier to look up refund for
    """
    result = await handle_get_refund_status(transaction_id)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def get_train_provider_status(provider_ref_id: str) -> str:
    """Fetch train ticket provider status by provider reference ID.

    Returns provider status including ticket_code, ticket_status,
    payment_status. Read-only.

    Args:
        provider_ref_id: The provider reference identifier
    """
    result = await handle_get_train_provider_status(provider_ref_id)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def get_utility_bill_status(provider_ref_id: str) -> str:
    """Fetch utility bill provider status by provider reference ID.

    Returns provider status including bill_code, customer_code,
    payment_status. Read-only.

    Args:
        provider_ref_id: The provider reference identifier
    """
    result = await handle_get_utility_bill_status(provider_ref_id)
    return json.dumps(result, ensure_ascii=False, default=str)


# ═══════════════════════════════════════════════════════════════
#  Draft-only tools (NO financial execution)
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def create_refund_request_draft(
    case_id: str,
    transaction_id: str,
    user_id: str,
    amount: int,
    reason: str,
    evidence_summary: list[str],
) -> str:
    """Create a refund request draft. Does NOT execute any refund.

    This creates a pending draft that requires human approval.
    The agent cannot refund money — only create a request draft.

    Args:
        case_id: The case this refund belongs to
        transaction_id: The transaction to refund
        user_id: The user who owns the transaction
        amount: Refund amount in VND (from wallet_ledger.debit_amount, NOT customer-claimed)
        reason: Human-readable reason for the refund
        evidence_summary: List of evidence items supporting the refund
    """
    result = await handle_create_refund_request_draft(
        case_id=case_id,
        transaction_id=transaction_id,
        user_id=user_id,
        amount=amount,
        reason=reason,
        evidence_summary=evidence_summary,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def create_reconciliation_ticket_draft(
    case_id: str,
    transaction_id: str,
    user_id: str,
    mismatch_type: str,
    evidence_summary: list[str],
    provider_ref_id: str = "",
) -> str:
    """Create a reconciliation ticket draft.

    For cases where provider hasn't confirmed payment. Creates a ticket
    for the operations team to investigate. No financial impact.

    Args:
        case_id: The case this ticket belongs to
        transaction_id: The mismatched transaction
        user_id: The user who owns the transaction
        mismatch_type: Type of mismatch detected
        evidence_summary: List of evidence items
        provider_ref_id: Optional provider reference ID
    """
    result = await handle_create_reconciliation_ticket_draft(
        case_id=case_id,
        transaction_id=transaction_id,
        user_id=user_id,
        mismatch_type=mismatch_type,
        evidence_summary=evidence_summary,
        provider_ref_id=provider_ref_id or None,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def create_customer_response_draft(
    case_id: str,
    transaction_id: str,
    message: str,
) -> str:
    """Create a customer response draft.

    Low-risk action. Creates a text response for customer communication.
    No approval required. No financial impact.

    Args:
        case_id: The case this response belongs to
        transaction_id: The related transaction
        message: The response message text
    """
    result = await handle_create_customer_response_draft(
        case_id=case_id,
        transaction_id=transaction_id,
        message=message,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def create_force_success_draft(
    case_id: str,
    transaction_id: str,
    user_id: str,
    amount: int,
    reason: str,
    evidence_summary: list[str],
) -> str:
    """Create a force-success draft for a pending wallet topup. HIGH-RISK.

    This does NOT update wallet balance, modify ledger, or mark
    transaction as success. It only creates a draft for human approval.

    Use case: bank already debited customer but wallet shows pending/0.
    After human approval, operations team manually processes the topup.

    Args:
        case_id: The case this draft belongs to
        transaction_id: The stuck pending transaction
        user_id: The user who owns the transaction
        amount: The topup amount in VND
        reason: Human-readable reason from diagnosis
        evidence_summary: List of evidence items (bank status, reconciliation, etc.)
    """
    result = await handle_create_force_success_draft(
        case_id=case_id,
        transaction_id=transaction_id,
        user_id=user_id,
        amount=amount,
        reason=reason,
        evidence_summary=evidence_summary,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


# ═══════════════════════════════════════════════════════════════
#  Fraud / Account Lock tools (Use Case 2)
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def get_account_status(user_id: str) -> str:
    """Fetch account status for a user.

    Returns account data including account_status, withdrawal_enabled,
    lock_reason, and current_balance. Read-only — does not modify any data.

    Args:
        user_id: The unique user identifier (e.g. U_FRAUD_001)
    """
    result = await handle_get_account_status(user_id)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def get_fraud_case(user_id: str) -> str:
    """Fetch fraud case for a user.

    Returns fraud case data including risk_score, risk_level,
    fraud_status, signals, and recommended_decision. Read-only.

    Args:
        user_id: The user identifier to look up fraud case for
    """
    result = await handle_get_fraud_case(user_id)
    return json.dumps(result, ensure_ascii=False, default=str)


# ═══════════════════════════════════════════════════════════════
#  Identity resolution tools (Use Case 2 Phase 1) — READ-ONLY
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def get_user_by_phone(phone: str) -> str:
    """Look up a user account by phone number. READ-ONLY.

    Returns account data including user_id, account_status, wallet_id.
    Used for identity resolution when customer provides phone but not user_id.

    Args:
        phone: Vietnamese phone number (e.g. 0981000001)
    """
    result = await handle_get_user_by_phone(phone)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def get_user_by_email(email: str) -> str:
    """Look up a user account by email address. READ-ONLY.

    Returns account data including user_id, account_status, wallet_id.
    Used for identity resolution when customer provides email but not user_id.

    Args:
        email: Email address to look up
    """
    result = await handle_get_user_by_email(email)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def get_user_by_wallet_id(wallet_id: str) -> str:
    """Look up a user account by wallet ID. READ-ONLY.

    Returns account data including user_id, account_status, wallet_id.
    Used for identity resolution when customer provides wallet_id but not user_id.

    Args:
        wallet_id: Wallet identifier (e.g. WALLET_FRAUD_001)
    """
    result = await handle_get_user_by_wallet_id(wallet_id)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def create_unlock_account_draft(
    case_id: str,
    user_id: str,
    reason: str,
    evidence_summary: list[str],
) -> str:
    """Create an unlock account draft. HIGH-RISK, DRAFT ONLY.

    This does NOT unlock the account, update account_status,
    or enable withdrawals. It only creates a draft for human approval.

    Use case: Fraud Detection false positive — account locked by mistake.

    Args:
        case_id: The case this draft belongs to
        user_id: The user whose account should be unlocked
        reason: Human-readable reason from diagnosis
        evidence_summary: List of evidence items supporting the unlock
    """
    result = await handle_create_unlock_account_draft(
        case_id=case_id,
        user_id=user_id,
        reason=reason,
        evidence_summary=evidence_summary,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def create_request_documents_response_draft(
    case_id: str,
    user_id: str,
    reason: str,
    evidence_summary: list[str],
) -> str:
    """Create a request-documents response draft. HIGH-RISK, DRAFT ONLY.

    Creates a draft requesting customer to provide verification documents.
    Does NOT modify account status. Account remains locked.

    Use case: Suspicious activity detected — need customer to verify identity.

    Args:
        case_id: The case this draft belongs to
        user_id: The user whose account is locked
        reason: Human-readable reason from diagnosis
        evidence_summary: List of evidence items
    """
    result = await handle_create_request_documents_response_draft(
        case_id=case_id,
        user_id=user_id,
        reason=reason,
        evidence_summary=evidence_summary,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


# ═══════════════════════════════════════════════════════════════
#  Merchant settlement read-only tools (Case 3)
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def get_merchant_profile(
    merchant_id: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    tax_code: str | None = None,
) -> str:
    """Fetch merchant profile by identity.

    Lookup priority: merchant_id > phone > email > tax_code.
    Returns merchant data including name, status, settlement_cycle.
    Read-only — does not modify any data.

    Args:
        merchant_id: The merchant identifier (e.g. MRC_001_BATCH_FAIL)
        phone: Merchant phone number
        email: Merchant contact email
        tax_code: Merchant tax code (Mã số thuế)
    """
    result = await handle_get_merchant_profile(
        merchant_id=merchant_id, phone=phone, email=email, tax_code=tax_code,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def get_merchant_bank_account(merchant_id: str) -> str:
    """Fetch merchant bank account details.

    Returns bank account data including account_number, bank_name,
    verification_status, is_active. Read-only.

    Args:
        merchant_id: The merchant identifier
    """
    result = await handle_get_merchant_bank_account(merchant_id)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def get_settlement_batch(
    settlement_date: str | None = None,
    cycle: str = "D+1",
    batch_id: str | None = None,
) -> str:
    """Fetch settlement batch information.

    Lookup priority: batch_id > settlement_date + cycle.
    Returns batch status, total_amount, failure_reason. Read-only.

    Args:
        settlement_date: Settlement date (YYYY-MM-DD)
        cycle: Settlement cycle (D+1, D+2). Default: D+1
        batch_id: Direct batch identifier
    """
    result = await handle_get_settlement_batch(
        settlement_date=settlement_date, cycle=cycle, batch_id=batch_id,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def get_merchant_settlement_ledger(
    merchant_id: str,
    settlement_date: str | None = None,
) -> str:
    """Fetch merchant settlement ledger (breakdown of amounts).

    Returns gross_amount, fee_amount, refund_amount, chargeback_amount,
    net_settlement_amount. Read-only.

    Args:
        merchant_id: The merchant identifier
        settlement_date: Optional settlement date (YYYY-MM-DD). If omitted, returns latest.
    """
    result = await handle_get_merchant_settlement_ledger(
        merchant_id=merchant_id, settlement_date=settlement_date,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def get_merchant_payout(
    merchant_id: str,
    settlement_date: str | None = None,
    payout_id: str | None = None,
) -> str:
    """Fetch merchant payout details.

    Lookup priority: payout_id > merchant_id + settlement_date > latest.
    Returns payout status, amount, bank_transfer_ref, failure_reason. Read-only.

    Args:
        merchant_id: The merchant identifier
        settlement_date: Optional settlement date (YYYY-MM-DD)
        payout_id: Direct payout identifier
    """
    result = await handle_get_merchant_payout(
        merchant_id=merchant_id, settlement_date=settlement_date, payout_id=payout_id,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def get_bnak_transfer_receipt(
    bank_transfer_ref: str | None = None,
    payout_id: str | None = None,
) -> str:
    """Fetch bank transfer receipt (UNC confirmation).

    Lookup priority: bank_transfer_ref > payout_id.
    Returns bank_status, unc_number, receipt_url. Read-only.

    Args:
        bank_transfer_ref: Bank transfer reference number
        payout_id: Payout identifier to look up receipt for
    """
    result = await handle_get_bank_transfer_receipt(
        bank_transfer_ref=bank_transfer_ref, payout_id=payout_id,
    )
    return json.dumps(result, ensure_ascii=False, default=str)
