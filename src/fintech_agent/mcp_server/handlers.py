"""MCP tool handlers — bridge between MCP tools and existing repository layer.

Each handler:
  - Calls the appropriate repository via repository_factory
  - Converts Pydantic models to dicts for MCP JSON serialization
  - Handles RecordNotFound gracefully
  - Draft handlers do NOT execute financial operations

Read-only handlers: get_transaction, get_reconciliation_status, get_wallet_ledger,
                     get_refund_status, get_train_provider_status, get_utility_bill_status

Draft-only handlers: create_refund_request_draft, create_reconciliation_ticket_draft,
                      create_customer_response_draft, create_force_success_draft
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fintech_agent.database.repository_factory import (
    get_ledger_repo,
    get_reconciliation_repo,
    get_refund_repo,
    get_train_provider_repo,
    get_transaction_repo,
    get_utility_provider_repo,
)
from fintech_agent.mcp_server.schemas import DraftOutput
from fintech_agent.repositories.base import RecordNotFound

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  Read-only handlers
# ═══════════════════════════════════════════════════════════════


async def handle_get_transaction(transaction_id: str) -> dict:
    """Fetch a transaction by its ID.

    Returns the transaction as a dict, or an error dict if not found.
    """
    try:
        repo = get_transaction_repo()
        txn = repo.get_by_id(transaction_id)
        return txn.model_dump(mode="json")
    except RecordNotFound:
        return {"error": f"Transaction not found: {transaction_id}"}
    except Exception as e:
        _logger.exception("handle_get_transaction failed for %s", transaction_id)
        return {"error": str(e)}


async def handle_get_reconciliation_status(transaction_id: str) -> dict:
    """Fetch reconciliation status for a transaction.

    Returns the reconciliation record as a dict, or an error dict if not found.
    """
    try:
        repo = get_reconciliation_repo()
        rec = repo.get_by_transaction_id(transaction_id)
        if rec is None:
            return {"error": f"No reconciliation record for: {transaction_id}"}
        return rec.model_dump(mode="json")
    except RecordNotFound:
        return {"error": f"No reconciliation record for: {transaction_id}"}
    except Exception as e:
        _logger.exception("handle_get_reconciliation_status failed for %s", transaction_id)
        return {"error": str(e)}


async def handle_get_wallet_ledger(transaction_id: str) -> dict:
    """Fetch wallet ledger entry for a transaction.

    Returns the ledger data as a dict, or an error dict if not found.
    """
    try:
        repo = get_ledger_repo()
        ledger = repo.get_by_transaction_id(transaction_id)
        return ledger.model_dump(mode="json")
    except RecordNotFound:
        return {"error": f"Wallet ledger not found for: {transaction_id}"}
    except Exception as e:
        _logger.exception("handle_get_wallet_ledger failed for %s", transaction_id)
        return {"error": str(e)}


async def handle_get_refund_status(transaction_id: str) -> dict:
    """Fetch refund status for a transaction.

    Returns the refund record as a dict, or an error dict if not found.
    """
    try:
        repo = get_refund_repo()
        refund = repo.get_by_transaction_id(transaction_id)
        return refund.model_dump(mode="json")
    except RecordNotFound:
        return {"error": f"Refund not found for: {transaction_id}"}
    except Exception as e:
        _logger.exception("handle_get_refund_status failed for %s", transaction_id)
        return {"error": str(e)}


async def handle_get_train_provider_status(provider_ref_id: str) -> dict:
    """Fetch train provider status by provider reference ID.

    Returns the provider status as a dict, or an error dict if not found.
    """
    try:
        repo = get_train_provider_repo()
        status = repo.get_by_ref_id(provider_ref_id)
        return status.model_dump(mode="json")
    except RecordNotFound:
        return {"error": f"Train provider status not found for: {provider_ref_id}"}
    except Exception as e:
        _logger.exception("handle_get_train_provider_status failed for %s", provider_ref_id)
        return {"error": str(e)}


async def handle_get_utility_bill_status(provider_ref_id: str) -> dict:
    """Fetch utility bill provider status by provider reference ID.

    Returns the provider status as a dict, or an error dict if not found.
    """
    try:
        repo = get_utility_provider_repo()
        status = repo.get_by_ref_id(provider_ref_id)
        return status.model_dump(mode="json")
    except RecordNotFound:
        return {"error": f"Utility bill status not found for: {provider_ref_id}"}
    except Exception as e:
        _logger.exception("handle_get_utility_bill_status failed for %s", provider_ref_id)
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
#  Draft-only handlers (NO financial execution)
# ═══════════════════════════════════════════════════════════════


async def handle_create_refund_request_draft(
    case_id: str,
    transaction_id: str,
    user_id: str,
    amount: int,
    reason: str,
    evidence_summary: list[str],
) -> dict:
    """Create a refund request draft. Does NOT execute any refund.

    Delegates to the existing draft_action_tools which:
      - Runs money_action_guard
      - Checks idempotency
      - Creates an in-memory draft
    """
    from fintech_agent.tools.draft_action_tools import (
        create_refund_request_draft,
    )

    try:
        result = create_refund_request_draft(
            case_id=case_id,
            transaction_id=transaction_id,
            user_id=user_id,
            amount=amount,
            reason=reason,
            evidence_summary=evidence_summary,
        )
        if result.success and result.draft:
            return DraftOutput(
                draft_id=f"DRAFT_REFUND_{transaction_id}",
                type="refund_request_draft",
                case_id=case_id,
                transaction_id=transaction_id,
                amount=amount,
                user_id=user_id,
                reason=reason,
                evidence_summary=evidence_summary,
            ).to_dict()
        return {"error": result.error or "Unknown error creating refund draft"}
    except Exception as e:
        _logger.exception("handle_create_refund_request_draft failed")
        return {"error": str(e)}


async def handle_create_reconciliation_ticket_draft(
    case_id: str,
    transaction_id: str,
    user_id: str,
    mismatch_type: str,
    evidence_summary: list[str],
    provider_ref_id: str | None = None,
) -> dict:
    """Create a reconciliation ticket draft.

    Delegates to the existing draft_action_tools.
    """
    from fintech_agent.tools.draft_action_tools import (
        create_reconciliation_ticket_draft,
    )

    try:
        result = create_reconciliation_ticket_draft(
            case_id=case_id,
            transaction_id=transaction_id,
            user_id=user_id,
            mismatch_type=mismatch_type,
            evidence_summary=evidence_summary,
            provider_ref_id=provider_ref_id,
        )
        if result.success and result.draft:
            return DraftOutput(
                draft_id=f"DRAFT_RECON_{transaction_id}",
                type="reconciliation_ticket_draft",
                case_id=case_id,
                transaction_id=transaction_id,
                user_id=user_id,
                reason=f"mismatch: {mismatch_type}",
                evidence_summary=evidence_summary,
                approval_required=False,
            ).to_dict()
        return {"error": result.error or "Unknown error creating reconciliation draft"}
    except Exception as e:
        _logger.exception("handle_create_reconciliation_ticket_draft failed")
        return {"error": str(e)}


async def handle_create_customer_response_draft(
    case_id: str,
    transaction_id: str,
    message: str,
) -> dict:
    """Create a customer response draft.

    Low-risk action — no approval required.
    Delegates to the existing draft_action_tools.
    """
    from fintech_agent.tools.draft_action_tools import (
        create_customer_response_draft,
    )

    try:
        result = create_customer_response_draft(
            case_id=case_id,
            transaction_id=transaction_id,
            message=message,
        )
        if result.success and result.draft:
            return DraftOutput(
                draft_id=f"DRAFT_RESPONSE_{case_id}",
                type="customer_response_draft",
                case_id=case_id,
                transaction_id=transaction_id,
                approval_required=False,
                note="Customer response draft. No approval required.",
            ).to_dict()
        return {"error": result.error or "Unknown error creating response draft"}
    except Exception as e:
        _logger.exception("handle_create_customer_response_draft failed")
        return {"error": str(e)}


async def handle_create_force_success_draft(
    case_id: str,
    transaction_id: str,
    user_id: str,
    amount: int,
    reason: str,
    evidence_summary: list[str],
) -> dict:
    """Create a force-success draft for a pending wallet topup.

    HIGH-RISK action — requires human approval.

    This does NOT:
      - Update wallet balance
      - Modify wallet ledger
      - Mark transaction as success
      - Execute any refund
      - Perform any write action

    It only creates a draft object for human review.
    Delegates to the existing draft_action_tools for safety/idempotency.
    """
    from fintech_agent.tools.draft_action_tools import (
        create_force_success_draft,
    )

    try:
        result = create_force_success_draft(
            case_id=case_id,
            transaction_id=transaction_id,
            user_id=user_id,
            amount=amount,
            reason=reason,
            evidence_summary=evidence_summary,
        )
        if result.success and result.draft:
            return DraftOutput(
                draft_id=f"DRAFT_FORCE_SUCCESS_{transaction_id}",
                type="force_success_draft",
                case_id=case_id,
                transaction_id=transaction_id,
                amount=amount,
                user_id=user_id,
                reason=reason,
                evidence_summary=evidence_summary,
                note="Draft only. Human approval required before any money-impacting action.",
            ).to_dict()
        return {"error": result.error or "Unknown error creating force success draft"}
    except Exception as e:
        _logger.exception("handle_create_force_success_draft failed")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
#  Fraud / Account Lock handlers (Use Case 2)
# ═══════════════════════════════════════════════════════════════


async def handle_get_account_status(user_id: str) -> dict:
    """Fetch account status for a user. READ-ONLY.

    Returns the account record as a dict, or empty dict if not found.
    """
    try:
        from fintech_agent.database.repository_factory import get_account_repo
        repo = get_account_repo()
        account = repo.get_account_status(user_id)
        if account is None:
            return {"error": f"Account not found: {user_id}"}
        return account.model_dump(mode="json")
    except Exception as e:
        _logger.exception("handle_get_account_status failed for %s", user_id)
        return {"error": str(e)}


async def handle_get_fraud_case(user_id: str) -> dict:
    """Fetch fraud case for a user. READ-ONLY.

    Returns the fraud case record as a dict, or empty dict if not found.
    """
    try:
        from fintech_agent.database.repository_factory import get_fraud_repo
        repo = get_fraud_repo()
        fraud_case = repo.get_fraud_case(user_id)
        if fraud_case is None:
            return {"error": f"Fraud case not found for user: {user_id}"}
        return fraud_case.model_dump(mode="json")
    except Exception as e:
        _logger.exception("handle_get_fraud_case failed for %s", user_id)
        return {"error": str(e)}


async def handle_create_unlock_account_draft(
    case_id: str,
    user_id: str,
    reason: str,
    evidence_summary: list[str],
) -> dict:
    """Create an unlock account draft. DRAFT ONLY — does NOT unlock account.

    This does NOT:
      - Update account_status
      - Set withdrawal_enabled = true
      - Modify any account record

    It only creates a draft object for human review.
    """
    from fintech_agent.safety.money_action_guard import guard_action
    guard_action("create_unlock_account_draft")  # This is SAFE (not in blocklist)

    return DraftOutput(
        draft_id=f"DRAFT_UNLOCK_{user_id}_{case_id}",
        type="unlock_account_draft",
        case_id=case_id,
        user_id=user_id,
        reason=reason,
        evidence_summary=evidence_summary,
        note="Draft only. Human approval required before unlocking account.",
    ).to_dict()


async def handle_create_request_documents_response_draft(
    case_id: str,
    user_id: str,
    reason: str,
    evidence_summary: list[str],
) -> dict:
    """Create a request-documents response draft. DRAFT ONLY.

    Creates a response requesting the customer to provide verification
    documents. Does NOT modify account status.
    """
    return DraftOutput(
        draft_id=f"DRAFT_REQUEST_DOCS_{user_id}_{case_id}",
        type="request_documents_response_draft",
        case_id=case_id,
        user_id=user_id,
        reason=reason,
        evidence_summary=evidence_summary,
        message=(
            "Tài khoản đang được tạm khóa do hệ thống phát hiện dấu hiệu bất thường. "
            "Vui lòng bổ sung giấy tờ/chứng minh giao dịch để bộ phận Risk/Fraud kiểm tra."
        ),
        note="Draft only. Account remains locked pending document review.",
    ).to_dict()

