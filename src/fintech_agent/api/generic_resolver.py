"""Generic case evidence resolver.

Single entry point for resolving customer-provided information against
backend data. Dispatches based on session type and workflow hint.

SECURITY INVARIANTS:
  - Identity comes from SERVER-SIDE session only, never frontend.
  - Only searches data belonging to the logged-in user/merchant.
  - Read-only: does not modify transactions, balances, or ledger.
  - Returns public_safe_evidence only, never raw internal fields.
  - No hard-coded mock records or transaction IDs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fintech_agent.llm.message_analyzer import (
    MessageAnalysis,
    ExtractedFields,
)

logger = logging.getLogger(__name__)


@dataclass
class ResolutionResult:
    """Generic resolution result."""
    resolution_status: str = "need_more_info"
    # resolved | multiple_candidates | no_match | need_more_info |
    # invalid_session | ownership_mismatch
    resolved_entity_type: str = "none"
    # transaction | bill | order | merchant_settlement | account | none
    resolved_entity_id: str | None = None
    public_safe_evidence: dict = field(default_factory=dict)
    missing_info: list[str] = field(default_factory=list)
    public_response: str = ""


# ─── Transaction Search ─────────────────────────────────────────

def _search_user_transactions(
    user_id: str,
    extracted: ExtractedFields,
    workflow_hint: str,
) -> list[Any]:
    """Search transactions belonging to user_id using extracted criteria.

    Generic: works with any combination of fields.
    """
    try:
        from fintech_agent.database.repository_factory import get_transaction_repo
        repo = get_transaction_repo()
    except Exception as exc:
        logger.error("[Resolver] Failed to get transaction repo: %s", exc)
        return []

    candidates = []

    # 1. Try direct ID lookup first
    if extracted.transaction_id:
        try:
            txn = repo.get_by_id(extracted.transaction_id)
            if txn and getattr(txn, "user_id", None) == user_id:
                return [txn]
        except Exception:
            pass

    if extracted.order_id:
        try:
            txn = repo.get_by_id(extracted.order_id)
            if txn and getattr(txn, "user_id", None) == user_id:
                return [txn]
        except Exception:
            pass

    # 2. Search all user transactions and filter by criteria
    try:
        # Get all transactions for user
        all_txns = []
        if hasattr(repo, "get_by_user_id"):
            all_txns = repo.get_by_user_id(user_id)
        elif hasattr(repo, "find_by_user_id"):
            all_txns = repo.find_by_user_id(user_id)
        elif hasattr(repo, "list_all"):
            all_txns = [
                t for t in repo.list_all()
                if getattr(t, "user_id", None) == user_id
            ]

        for txn in all_txns:
            score = 0

            # Match amount
            if extracted.amount and hasattr(txn, "amount"):
                try:
                    txn_amount = int(txn.amount) if txn.amount else 0
                    if txn_amount == extracted.amount:
                        score += 3
                    elif abs(txn_amount - extracted.amount) < extracted.amount * 0.1:
                        score += 1
                except (ValueError, TypeError):
                    pass

            # Match bank
            if extracted.bank_name and hasattr(txn, "bank_code"):
                if txn.bank_code and extracted.bank_name.upper() in txn.bank_code.upper():
                    score += 2

            # Match bank reference
            if extracted.bank_reference and hasattr(txn, "bank_reference"):
                if txn.bank_reference and extracted.bank_reference in txn.bank_reference:
                    score += 4  # Strong signal

            # Match service type / workflow
            if workflow_hint and workflow_hint != "unknown" and hasattr(txn, "service_type"):
                svc = getattr(txn, "service_type", "") or ""
                if workflow_hint.replace("_", " ") in svc.lower().replace("_", " "):
                    score += 1

            # Minimum threshold: workflow-only match (score=1) is too weak.
            # Need at least one concrete field match (amount, bank, ref).
            if score >= 2:
                candidates.append((score, txn))

    except Exception as exc:
        logger.error("[Resolver] Error searching user transactions: %s", exc)

    # Sort by score descending
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [txn for _, txn in candidates]


def _resolve_wallet_user(
    session: dict,
    extracted: ExtractedFields,
    workflow_hint: str,
    message_type: str,
) -> ResolutionResult:
    """Resolve evidence for wallet_user session."""
    user_id = session.get("user_id", "")
    if not user_id:
        return ResolutionResult(
            resolution_status="invalid_session",
            public_response="Không xác định được tài khoản. Vui lòng đăng nhập lại.",
        )

    # Check if any searchable fields are provided
    has_search_criteria = any([
        extracted.transaction_id, extracted.order_id, extracted.bill_code,
        extracted.amount, extracted.bank_name, extracted.bank_reference,
        extracted.approximate_time_text, extracted.approximate_date_text,
    ])

    if not has_search_criteria:
        # Nothing to search — determine what's needed based on workflow
        return ResolutionResult(
            resolution_status="need_more_info",
            resolved_entity_type="none",
            missing_info=_infer_missing_info(workflow_hint),
        )

    matches = _search_user_transactions(user_id, extracted, workflow_hint)

    if len(matches) == 1:
        txn = matches[0]
        txn_id = getattr(txn, "transaction_id", "") or getattr(txn, "id", "")
        evidence = _txn_to_safe_evidence(txn)

        # Enrich with workflow-appropriate downstream evidence (read-only).
        # wallet_topup → reconciliation (wallet credit);
        # train_ticket → train provider (ticket issuing);
        # utility_bill → utility provider (bill confirmation).
        _enrich_evidence_for_workflow(txn, txn_id, workflow_hint, evidence)

        return ResolutionResult(
            resolution_status="resolved",
            resolved_entity_type="transaction",
            resolved_entity_id=txn_id,
            public_safe_evidence=evidence,
            public_response=(
                f"Chúng tôi đã tìm thấy giao dịch phù hợp. "
                f"Đang kiểm tra chi tiết và sẽ cập nhật kết quả sớm nhất."
            ),
        )

    if len(matches) > 1:
        return ResolutionResult(
            resolution_status="multiple_candidates",
            resolved_entity_type="transaction",
            public_response=(
                f"Chúng tôi tìm thấy {len(matches)} giao dịch phù hợp. "
                f"Bạn có thể cho biết thêm thời gian chính xác hoặc "
                f"mã tham chiếu ngân hàng để chúng tôi xác định đúng giao dịch?"
            ),
            missing_info=["transaction_time", "bank_reference"],
        )

    # No match — suggest more specific info (bank ref, exact date)
    # Don't repeat fields the customer already provided
    remaining_missing = _infer_missing_info(workflow_hint)
    # Subtract fields we already have from extraction
    for attr_name in ("amount", "bank_name", "approximate_time_text", "approximate_date_text"):
        val = getattr(extracted, attr_name, None)
        if val:
            # Map extraction attr to missing_fields key
            key_map = {
                "amount": "amount",
                "bank_name": "bank_name",
                "approximate_time_text": "transaction_time",
                "approximate_date_text": "transaction_time",
            }
            field_key = key_map.get(attr_name, attr_name)
            remaining_missing = [f for f in remaining_missing if f != field_key]

    return ResolutionResult(
        resolution_status="no_match",
        resolved_entity_type="none",
        public_response=(
            "Chúng tôi chưa tìm thấy giao dịch phù hợp với thông tin đã cung cấp. "
            "Bạn vui lòng kiểm tra lại ngày giao dịch hoặc gửi mã tham chiếu "
            "ngân hàng trên biên lai nếu có."
        ),
        missing_info=remaining_missing,
    )


def _resolve_merchant(
    session: dict,
    extracted: ExtractedFields,
    workflow_hint: str,
) -> ResolutionResult:
    """Resolve evidence for merchant session."""
    merchant_id = session.get("merchant_id", "")
    if not merchant_id:
        return ResolutionResult(
            resolution_status="invalid_session",
            public_response="Không xác định được tài khoản merchant. Vui lòng đăng nhập lại.",
        )

    # Merchant settlement searches use merchant_id from session (trusted)
    # + payout_id/batch_id from customer message
    return ResolutionResult(
        resolution_status="need_more_info",
        resolved_entity_type="merchant_settlement",
        missing_info=["payout_id", "batch_id", "settlement_date"],
        public_response=(
            "Để kiểm tra khoản thanh toán, bạn có thể cung cấp "
            "mã payout, mã lô thanh toán, hoặc ngày settlement."
        ),
    )


def _resolve_account_lock(
    session: dict,
    extracted: ExtractedFields,
) -> ResolutionResult:
    """Resolve evidence for account lock cases."""
    user_id = session.get("user_id", "")
    return ResolutionResult(
        resolution_status="resolved",
        resolved_entity_type="account",
        resolved_entity_id=user_id,
        public_safe_evidence={
            "account_status": "under_review",
        },
        missing_info=["time_locked", "screenshot", "recent_activity"],
    )


# ─── Helpers ────────────────────────────────────────────────────

def _infer_missing_info(workflow_hint: str) -> list[str]:
    """Infer what info is typically needed based on workflow."""
    from fintech_agent.llm.message_analyzer import get_workflow_policy

    wf_policy = get_workflow_policy(workflow_hint)
    return list(wf_policy.get("safe_missing_info", ["transaction_id", "amount", "transaction_time"]))


def _enrich_evidence_for_workflow(
    txn: Any,
    txn_id: str,
    workflow_hint: str,
    evidence: dict,
) -> None:
    """Add workflow-appropriate downstream evidence (read-only, in place).

    The downstream stage differs per workflow, so we look up the RIGHT source
    of truth — never wallet reconciliation for a train/utility case:
      - wallet_topup → reconciliation (bank_status, wallet_status)
      - train_ticket → train provider (ticket_status, provider_status)
      - utility_bill → utility provider (bill_status, provider_status)
    """
    wf = (workflow_hint or "").lower()

    if wf == "train_ticket":
        evidence.update(_lookup_train_provider(txn))
        return

    if wf == "utility_bill":
        evidence.update(_lookup_utility_provider(txn))
        return

    # wallet_topup (and default): reconciliation describes the wallet credit step.
    if wf in ("wallet_topup", "", "unknown") and txn_id:
        recon = _lookup_reconciliation(txn_id)
        if recon:
            evidence.update(recon)


def _lookup_train_provider(txn: Any) -> dict:
    """Look up train provider status → public-safe ticket_status/provider_status."""
    provider_ref = getattr(txn, "provider_ref_id", None)
    if not provider_ref:
        return {}
    try:
        from fintech_agent.database.repository_factory import get_train_provider_repo
        repo = get_train_provider_repo()
        status = repo.get_by_ref_id(provider_ref)
    except Exception as exc:
        logger.warning("[Resolver] Train provider lookup failed: %s", exc)
        return {}

    booking = str(getattr(status, "booking_status", "") or "").lower()
    issued = booking in ("ticket_issued", "confirmed")
    pending = booking in ("booking_pending", "pending")
    if issued:
        ticket_status = "issued"
    elif pending:
        ticket_status = "pending"
    else:
        ticket_status = "not_issued"
    return {
        "ticket_status": ticket_status,
        "provider_status": "confirmed" if issued else (
            "pending" if pending else "not_confirmed"
        ),
    }


def _lookup_utility_provider(txn: Any) -> dict:
    """Look up utility provider status → public-safe bill_status/provider_status."""
    provider_ref = getattr(txn, "provider_ref_id", None)
    if not provider_ref:
        return {}
    try:
        from fintech_agent.database.repository_factory import get_utility_provider_repo
        repo = get_utility_provider_repo()
        status = repo.get_by_ref_id(provider_ref)
    except Exception as exc:
        logger.warning("[Resolver] Utility provider lookup failed: %s", exc)
        return {}

    prov = str(getattr(status, "provider_status", "") or "").lower()
    confirmed = prov in ("confirmed",)
    pending = prov in ("pending",)
    if confirmed:
        bill_status = "confirmed"
    elif pending:
        bill_status = "pending"
    else:
        bill_status = "not_confirmed"
    return {
        "bill_status": bill_status,
        "provider_status": "confirmed" if confirmed else (
            "pending" if pending else "not_confirmed"
        ),
    }


def _lookup_reconciliation(transaction_id: str) -> dict:
    """Query reconciliation for a transaction (read-only).

    Returns public-safe dict with bank_status, wallet_status, mismatch_type.
    Never exposes bank_ref_id, ticket_id, or internal notes.
    """
    try:
        from fintech_agent.database.repository_factory import (
            get_reconciliation_repo,
        )
        repo = get_reconciliation_repo()
        recon = repo.get_by_transaction_id(transaction_id)
        if recon is None:
            return {}

        safe: dict = {}

        # Bank status
        bank_status = getattr(recon, "bank_status", None)
        if bank_status:
            bs = str(bank_status).lower()
            if bs in ("success", "completed", "confirmed"):
                safe["bank_status"] = "success"
            elif bs in ("pending", "processing"):
                safe["bank_status"] = "pending"
            elif bs in ("failed", "error"):
                safe["bank_status"] = "failed"
            else:
                safe["bank_status"] = bs

        # Wallet received
        wallet_received = getattr(recon, "money_received_in_master_wallet", None)
        if wallet_received is not None:
            safe["wallet_status"] = "received" if wallet_received else "not_received"

        # Mismatch type (already public-safe label)
        mismatch = getattr(recon, "mismatch_type", None)
        if mismatch:
            safe["mismatch_type"] = str(mismatch)

        # Reconciliation overall status
        recon_status = getattr(recon, "status", None)
        if recon_status:
            safe["reconciliation_status"] = str(recon_status)

        return safe

    except Exception as exc:
        logger.warning("[Resolver] Reconciliation lookup failed: %s", exc)
        return {}


def _txn_to_safe_evidence(txn: Any) -> dict:
    """Convert a transaction object to public-safe evidence dict."""
    safe = {}

    if hasattr(txn, "status"):
        status = str(txn.status).lower()
        if status in ("pending", "processing"):
            safe["transaction_status"] = "đang xử lý"
        elif status in ("success", "completed"):
            safe["transaction_status"] = "đã hoàn thành"
        elif status in ("failed", "error"):
            safe["transaction_status"] = "gặp lỗi"
        else:
            safe["transaction_status"] = "đang kiểm tra"

    if hasattr(txn, "amount") and txn.amount:
        try:
            safe["amount"] = f"{int(txn.amount):,} VND".replace(",", ".")
        except (ValueError, TypeError):
            pass

    if hasattr(txn, "created_at") and txn.created_at:
        safe["time"] = str(txn.created_at)

    return safe


# ─── Public Entry Point ────────────────────────────────────────

def resolve_case_evidence(
    session_context: dict | None,
    active_case_context: dict | None,
    message_analysis: MessageAnalysis,
) -> ResolutionResult:
    """Generic entry point for resolving customer case evidence.

    Dispatches based on session type and workflow hint.

    Args:
        session_context: Server-side session dict (user_id, merchant_id, etc.)
        active_case_context: Active case state dict.
        message_analysis: Output from analyze_customer_message.

    Returns:
        ResolutionResult with resolution_status and public_safe_evidence.
    """
    if session_context is None:
        return ResolutionResult(
            resolution_status="invalid_session",
            public_response="Vui lòng đăng nhập để hệ thống xác minh tài khoản.",
        )

    subject_type = session_context.get("subject_type", "")
    workflow = message_analysis.workflow_hint or ""
    extracted = message_analysis.extracted

    # Account lock is a special flow regardless of subject type
    if workflow == "fraud_account_lock":
        return _resolve_account_lock(session_context, extracted)

    if subject_type == "wallet_user":
        return _resolve_wallet_user(
            session_context, extracted, workflow, message_analysis.message_type,
        )

    if subject_type == "merchant":
        return _resolve_merchant(session_context, extracted, workflow)

    # Unknown subject type — can't resolve
    return ResolutionResult(
        resolution_status="need_more_info",
        public_response="Vui lòng mô tả chi tiết vấn đề bạn đang gặp.",
    )
