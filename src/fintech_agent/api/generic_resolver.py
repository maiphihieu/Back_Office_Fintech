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
    # resolved | multiple_candidates | no_match | amount_mismatch |
    # need_more_info | invalid_session | ownership_mismatch | evidence_error
    resolved_entity_type: str = "none"
    # transaction | bill | order | merchant_settlement | account | none
    resolved_entity_id: str | None = None
    public_safe_evidence: dict = field(default_factory=dict)
    missing_info: list[str] = field(default_factory=list)
    public_response: str = ""
    # ── Verified evidence (from DB/tool, NOT from customer message) ──
    verified_amount: int | None = None
    verified_status: str = ""
    verified_owner_id: str = ""
    verified_bank_name: str = ""
    verified_bank_status: str = ""
    verified_provider_status: str = ""
    # ── Candidate evidence (broader search when exact match fails) ──
    candidate_evidence: list[dict] = field(default_factory=list)
    # ── Claim-vs-evidence binding (amount_mismatch) ──
    # The customer's claimed amount when it does NOT match any verified
    # transaction, while a verified problematic transaction with a DIFFERENT
    # amount exists on the logged-in account. The claim is never merged into
    # the verified fields above.
    claimed_amount: int | None = None
    no_exact_claim_match: bool = False


# ─── No-match customer messages (policy templates, safe + honest) ──
#
# When the logged-in account has no matching data, the bot says so clearly and
# immediately — it must never imply the transaction exists / is being processed.
NO_MATCH_ID_RESPONSE = (
    "Hiện tại hệ thống chưa tìm thấy mã này trong tài khoản đang đăng nhập của bạn. "
    "Bạn vui lòng kiểm tra lại mã hoặc cung cấp mã tham chiếu/biên lai nếu có."
)
# Human noun per workflow — now read from the workflow registry.
# Kept as a fallback dict for backward compatibility; the registry is
# the source of truth.
_WORKFLOW_NOUN: dict[str, str] = {
    "wallet_topup": "giao dịch nạp tiền",
    "train_ticket": "giao dịch vé tàu",
    "utility_bill": "giao dịch thanh toán hóa đơn",
    "merchant_settlement_delay": "giao dịch settlement",
}


def _get_workflow_noun(workflow: str) -> str:
    """Get display noun from the registry, falling back to the legacy dict."""
    try:
        from fintech_agent.workflows.workflow_registry import get_registry
        registry = get_registry()
        noun = registry.get_display_noun(workflow)
        if noun and noun != "giao dịch":
            return noun
    except Exception:
        pass
    return _WORKFLOW_NOUN.get((workflow or "").lower(), "giao dịch")


def no_match_message(workflow: str = "") -> str:
    """Workflow-aware 'not found on your account' message (asks for stronger proof)."""
    noun = _get_workflow_noun(workflow)
    return (
        f"Hiện tại hệ thống chưa tìm thấy {noun} phù hợp trên tài khoản đang đăng nhập. "
        "Bạn vui lòng kiểm tra lại thời gian, số tiền hoặc gửi mã tham chiếu "
        "ngân hàng/biên lai nếu có."
    )


def multiple_candidates_message(workflow: str, count: int) -> str:
    """Ask the customer for ONE narrowing field when several records match."""
    noun = _get_workflow_noun(workflow)
    return (
        f"Chúng tôi tìm thấy {count} {noun} trên tài khoản đang đăng nhập của bạn. "
        "Bạn vui lòng cho biết thêm thời gian giao dịch (hoặc số tiền, hoặc mã đơn) "
        "để chúng tôi xác định đúng giao dịch cần kiểm tra."
    )


def _fmt_vnd(amount: int | float | None) -> str:
    """Format a VND amount like '500.000đ' (no hard-coded values)."""
    try:
        return f"{int(amount):,}đ".replace(",", ".")
    except (TypeError, ValueError):
        return str(amount)


def amount_mismatch_message(workflow: str, claimed: int, verified: int) -> str:
    """Claim-vs-evidence correction, built from DYNAMIC values.

    Clearly separates "bạn cung cấp" (claim) from "theo kiểm tra hệ thống"
    (verified evidence) and states that no transaction matching the CLAIMED
    amount exists on the logged-in account.
    """
    noun = _get_workflow_noun(workflow)
    return (
        f"Bạn cung cấp số tiền {_fmt_vnd(claimed)}. "
        f"Tuy nhiên, theo kiểm tra hệ thống trên tài khoản đang đăng nhập, "
        f"{noun} được ghi nhận là {_fmt_vnd(verified)}. "
        f"Hiện chưa tìm thấy {noun} {_fmt_vnd(claimed)} khớp với tài khoản này. "
        f"Nếu bạn chắc chắn có khoản {_fmt_vnd(claimed)}, vui lòng gửi mã tham chiếu "
        "ngân hàng hoặc biên lai để đối chiếu. Vui lòng không gửi PIN, OTP hoặc mật khẩu."
    )


# Generic default (workflow unknown) kept for callers/tests that import it.
NO_MATCH_CRITERIA_RESPONSE = no_match_message("")
# Customer insists they made the transaction but the system still finds nothing.
NO_MATCH_INSIST_RESPONSE = (
    "Mình hiểu bạn chắc chắn đã thực hiện giao dịch. Tuy nhiên, theo dữ liệu hiện "
    "tại, hệ thống chưa tìm thấy giao dịch khớp với tài khoản đang đăng nhập. "
    "Để đối chiếu chính xác hơn, bạn có thể gửi mã tham chiếu ngân hàng hoặc ảnh "
    "biên lai nếu có. Vui lòng không gửi PIN, OTP hoặc mật khẩu."
)


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

    if has_search_criteria:
        matches = _search_user_transactions(user_id, extracted, workflow_hint)
    else:
        # The customer is authenticated on their OWN account, so we don't need
        # them to provide anything: proactively scan their account for the
        # transaction that needs attention and diagnose it directly.
        problematic = _find_problematic_user_transactions(user_id, workflow_hint)
        if len(problematic) == 1:
            return _resolved_from_txn(problematic[0], user_id, workflow_hint)
        if len(problematic) > 1:
            # Several records need attention → ask for ONE narrowing field.
            return ResolutionResult(
                resolution_status="multiple_candidates",
                resolved_entity_type="transaction",
                public_response=multiple_candidates_message(workflow_hint, len(problematic)),
                missing_info=["transaction_time"],
            )
        wf = (workflow_hint or "").lower()
        if wf and wf != "unknown":
            # We know what the customer is complaining about, but their account
            # has no such record → say so clearly (no_match). Never pretend it
            # exists or interrogate blindly.
            return ResolutionResult(
                resolution_status="no_match",
                resolved_entity_type="none",
                public_response=no_match_message(workflow_hint),
                missing_info=_infer_missing_info(workflow_hint),
            )
        # Workflow still unknown → ask one gentle clarifier (what's the issue).
        return ResolutionResult(
            resolution_status="need_more_info",
            resolved_entity_type="none",
            missing_info=_infer_missing_info(workflow_hint),
        )

    if len(matches) == 1:
        return _resolved_from_txn(matches[0], user_id, workflow_hint)

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

    # Distinguish: customer gave an explicit id that wasn't found in THEIR
    # account, vs. only soft criteria (amount/time/bank) with no match.
    gave_id = bool(
        extracted.transaction_id or extracted.order_id or extracted.bill_code
    )

    # ── Claim-vs-account fallback (generic, reusable across workflows) ──
    # The claimed criteria matched nothing, but before concluding "no match"
    # we check whether the logged-in account DOES have a problematic
    # transaction for this workflow. If it does and the customer claimed a
    # different amount, that is an amount_mismatch: surface the verified
    # record + the unmatched claim instead of hiding the evidence. The claim
    # is NEVER merged into the verified fields.
    if not gave_id and extracted.amount:
        problematic = _find_problematic_user_transactions(user_id, workflow_hint)
        if len(problematic) == 1:
            base = _resolved_from_txn(problematic[0], user_id, workflow_hint)
            try:
                claimed = int(extracted.amount)
            except (TypeError, ValueError):
                claimed = None
            if claimed is not None and base.verified_amount is not None \
                    and claimed != base.verified_amount:
                base.resolution_status = "amount_mismatch"
                base.claimed_amount = claimed
                base.no_exact_claim_match = True
                base.public_response = ""  # composed by the pipeline from values
                base.missing_info = ["bank_reference"]
                return base

    return ResolutionResult(
        resolution_status="no_match",
        resolved_entity_type="none",
        public_response=NO_MATCH_ID_RESPONSE if gave_id else no_match_message(workflow_hint),
        missing_info=remaining_missing,
    )


# Workflow → transaction service_types it covers (for proactive account scan).
# Legacy dict kept as fallback; the registry is the source of truth.
_WORKFLOW_SERVICE_TYPES: dict[str, set[str]] = {
    "wallet_topup": {"wallet_topup"},
    "train_ticket": {"train_ticket"},
    "utility_bill": {"electric_bill", "water_bill"},
}


def _get_service_types(workflow_hint: str) -> set[str] | None:
    """Get service types from registry, falling back to legacy dict."""
    try:
        from fintech_agent.workflows.workflow_registry import get_registry
        registry = get_registry()
        svc = registry.get_service_types(workflow_hint)
        if svc is not None:
            return set(svc)
    except Exception:
        pass
    return _WORKFLOW_SERVICE_TYPES.get((workflow_hint or "").lower())
# A transaction "needs attention" when it has not cleanly completed.
_NEEDS_ATTENTION_STATUSES: frozenset[str] = frozenset({
    "pending", "processing", "in_progress", "failed", "error",
})


def _all_user_transactions(repo: Any, user_id: str) -> list[Any]:
    """Return every transaction belonging to user_id (read-only, repo-agnostic)."""
    if hasattr(repo, "get_by_user_id"):
        return list(repo.get_by_user_id(user_id) or [])
    if hasattr(repo, "find_by_user_id"):
        return list(repo.find_by_user_id(user_id) or [])
    if hasattr(repo, "list_all"):
        return [t for t in repo.list_all() if getattr(t, "user_id", None) == user_id]
    return []


def _transaction_needs_attention(txn: Any) -> bool:
    """True when a transaction has not cleanly completed for the CUSTOMER.

    Payment status alone is not enough: a train/utility payment can be
    'completed' while the downstream service (ticket issuing / provider
    confirmation) failed — which is exactly the "đã thanh toán nhưng chưa nhận"
    complaint. So for those services we also consult the provider record.
    """
    status = str(getattr(txn, "status", "") or "").lower()
    if status in _NEEDS_ATTENTION_STATUSES:
        return True
    if status not in ("success", "completed", "confirmed"):
        return False

    # Payment looks settled — check the downstream service for this txn type.
    svc = str(getattr(txn, "service_type", "") or "").lower()
    if svc == "train_ticket":
        return _lookup_train_provider(txn).get("ticket_status") != "issued"
    if svc in ("electric_bill", "water_bill"):
        return _lookup_utility_provider(txn).get("bill_status") != "confirmed"
    return False


def _find_problematic_user_transactions(
    user_id: str,
    workflow_hint: str,
) -> list[Any]:
    """Scan the logged-in user's own transactions for ones needing attention.

    Generic + data-driven: filters by the workflow's service types (when known)
    and keeps the transactions that have not cleanly completed — including
    paid-but-undelivered cases (ticket not issued / provider not confirmed).
    Most recent first. This is what lets the bot diagnose without interrogating
    the customer.
    """
    try:
        from fintech_agent.database.repository_factory import get_transaction_repo
        repo = get_transaction_repo()
    except Exception as exc:
        logger.error("[Resolver] Failed to get transaction repo: %s", exc)
        return []

    services = _get_service_types(workflow_hint)
    found: list[Any] = []
    try:
        for txn in _all_user_transactions(repo, user_id):
            svc = str(getattr(txn, "service_type", "") or "").lower()
            if services and svc not in services:
                continue
            if _transaction_needs_attention(txn):
                found.append(txn)
    except Exception as exc:
        logger.error("[Resolver] Account scan failed for %s: %s", user_id, exc)
        return []

    found.sort(key=lambda t: str(getattr(t, "created_at", "") or ""), reverse=True)
    logger.info(
        "[Resolver] Proactive scan user=%s workflow=%s → %d transaction(s) need attention",
        user_id, workflow_hint or "(any)", len(found),
    )
    return found


def _resolved_from_txn(
    txn: Any,
    user_id: str,
    workflow_hint: str,
) -> ResolutionResult:
    """Build a `resolved` ResolutionResult from a concrete transaction record.

    Shared by the criteria search and the proactive account scan so both paths
    produce the same workflow-aware, verified evidence.
    """
    txn_id = getattr(txn, "transaction_id", "") or getattr(txn, "id", "")
    evidence = _txn_to_safe_evidence(txn)

    # Enrich with the workflow's downstream source of truth (read-only).
    _enrich_evidence_for_workflow(txn, txn_id, workflow_hint, evidence)

    # ── Verified evidence straight from the DB record (never customer claims) ──
    v_amount = None
    if getattr(txn, "amount", None) is not None:
        try:
            v_amount = int(txn.amount)
        except (ValueError, TypeError):
            pass

    v_status = str(getattr(txn, "status", "") or "").lower()
    v_bank = str(getattr(txn, "bank_code", "") or "")

    return ResolutionResult(
        resolution_status="resolved",
        resolved_entity_type="transaction",
        resolved_entity_id=txn_id,
        public_safe_evidence=evidence,
        public_response=(
            "Chúng tôi đã tìm thấy giao dịch phù hợp. "
            "Đang kiểm tra chi tiết và sẽ cập nhật kết quả sớm nhất."
        ),
        verified_amount=v_amount,
        verified_status=v_status,
        verified_owner_id=user_id,
        verified_bank_name=v_bank,
        verified_bank_status=evidence.get("bank_status", ""),
        verified_provider_status=evidence.get("provider_status", ""),
    )


def _lookup_merchant_settlement(merchant_id: str, payout_id: str | None) -> dict:
    """Look up the logged-in merchant's settlement/payout status (public-safe).

    Returns {payment_status, payout_status, settlement_status, bank_account_status}
    drawn from the merchant's own records, or {} if nothing is found. NEVER
    returns raw payout ids, bank account numbers, or internal failure details.
    """
    try:
        from fintech_agent.database.repository_factory import get_merchant_settlement_repo
        repo = get_merchant_settlement_repo()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Resolver] Merchant settlement repo unavailable: %s", exc)
        return {}

    def _safe_call(fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception:  # noqa: BLE001
            return None

    payout = _safe_call(repo.get_merchant_payout, merchant_id, payout_id=payout_id)
    ledger = _safe_call(repo.get_merchant_settlement_ledger, merchant_id)
    bank = _safe_call(repo.get_merchant_bank_account, merchant_id)

    if payout is None and ledger is None and bank is None:
        return {}

    safe: dict = {}
    if ledger is not None:
        # A settlement ledger means the payment side is recorded in the system.
        safe["payment_status"] = "success"
        if getattr(ledger, "status", None):
            safe["settlement_status"] = str(ledger.status).lower()
    if payout is not None and getattr(payout, "status", None):
        safe["payout_status"] = str(payout.status).lower()
    if bank is not None and getattr(bank, "verification_status", None):
        safe["bank_account_status"] = str(bank.verification_status).lower()
    return safe


def _resolve_merchant(
    session: dict,
    extracted: ExtractedFields,
    workflow_hint: str,
) -> ResolutionResult:
    """Resolve merchant settlement from the logged-in merchant's own data.

    Identity is the trusted session.merchant_id — never from the message body.
    We proactively look up the merchant's settlement/payout/bank-account status
    instead of asking for a payout id first.
    """
    merchant_id = session.get("merchant_id", "")
    if not merchant_id:
        return ResolutionResult(
            resolution_status="invalid_session",
            public_response="Không xác định được tài khoản merchant. Vui lòng đăng nhập lại.",
        )

    payout_id = getattr(extracted, "payout_id", None)
    evidence = _lookup_merchant_settlement(merchant_id, payout_id)

    if not evidence:
        # No settlement/payout record on the logged-in merchant account.
        return ResolutionResult(
            resolution_status="no_match",
            resolved_entity_type="merchant_settlement",
            public_response=no_match_message("merchant_settlement_delay"),
            missing_info=["payout_id", "batch_id", "settlement_date"],
        )

    return ResolutionResult(
        resolution_status="resolved",
        resolved_entity_type="merchant_settlement",
        resolved_entity_id=merchant_id,
        public_safe_evidence=evidence,
        verified_owner_id=merchant_id,
        verified_status=evidence.get("payout_status", "") or evidence.get("settlement_status", ""),
        missing_info=[],
    )


def no_lock_evidence_message() -> str:
    """Honest 'no lock/restriction record found' reply (asks for safe info only)."""
    return (
        "Theo kiểm tra dữ liệu hiện tại, hệ thống chưa tìm thấy bản ghi khóa hoặc "
        "hạn chế tài khoản cho tài khoản đang đăng nhập. Nếu bạn vẫn không đăng "
        "nhập/giao dịch được, bạn vui lòng cho biết thời điểm gặp lỗi và ảnh chụp "
        "màn hình thông báo lỗi (nếu có) để chúng tôi kiểm tra thêm. "
        "Vui lòng không gửi mã PIN, OTP hoặc mật khẩu."
    )


def _lookup_account_status(user_id: str) -> dict | None:
    """Look up the customer's real account lock status (public-safe).

    Returns:
      - dict with account_record_found/lock_evidence_found/account_status/...
        when an account record exists,
      - {"account_record_found": False} when the account has NO record,
      - None on a lookup/system error (caller must not fabricate a status).
    NEVER returns fraud score, fraud status, or device signals.
    """
    try:
        from fintech_agent.database.repository_factory import get_account_repo
        acct = get_account_repo().get_account_status(user_id)
    except Exception as exc:  # noqa: BLE001 — chat must not break on lookup error
        logger.warning("[Resolver] Account status lookup failed: %s", exc)
        return None
    if acct is None:
        return {"account_record_found": False, "lock_evidence_found": False}

    raw = str(getattr(acct, "account_status", "") or "").lower()
    if raw in ("active", "normal", "ok", "enabled"):
        status = "active"
    elif raw in ("locked", "frozen", "suspended"):
        status = "locked"
    elif raw in ("restricted", "limited"):
        status = "restricted"
    elif raw in ("under_review", "reviewing", "pending_review"):
        status = "under_review"
    else:
        status = raw or "unknown"

    we = getattr(acct, "withdrawal_enabled", None)
    locked_at = getattr(acct, "locked_at", None)
    lock_reason = (
        getattr(acct, "lock_reason", None) or getattr(acct, "locked_reason", None)
    )

    # Lock evidence requires REAL data: a locked/restricted/review status,
    # withdrawals disabled, or a recorded lock timestamp — never the claim.
    lock_evidence_found = bool(
        status in ("locked", "restricted", "under_review")
        or we is False
        or locked_at is not None
    )

    safe: dict = {
        "account_record_found": True,
        "lock_evidence_found": lock_evidence_found,
        "account_status": status,
    }
    if we is not None:
        safe["withdrawal_enabled"] = bool(we)  # customer already knows this
    if locked_at is not None:
        safe["locked_at"] = str(locked_at)
    if lock_reason and lock_evidence_found:
        # Only a public-safe presence marker — never the internal reason text.
        safe["lock_reason_recorded"] = True
    return safe


def _resolve_account_lock(
    session: dict,
    extracted: ExtractedFields,
) -> ResolutionResult:
    """Resolve account-lock cases from the logged-in account's real status.

    The system determines lock status from its OWN data — not from customer
    screenshots — so we look it up and conclude (locked / active / restricted)
    instead of looping for evidence the customer cannot provide.
    """
    user_id = session.get("user_id", "")
    if not user_id:
        return ResolutionResult(
            resolution_status="invalid_session",
            public_response="Không xác định được tài khoản. Vui lòng đăng nhập lại.",
        )

    evidence = _lookup_account_status(user_id)

    if evidence is None:
        # Transient lookup error — say the check is unavailable; NEVER fabricate
        # a lock/review status from the customer's claim.
        return ResolutionResult(
            resolution_status="evidence_error",
            resolved_entity_type="account",
            resolved_entity_id=user_id,
            public_response=(
                "Hệ thống tạm thời chưa tra cứu được trạng thái tài khoản của bạn. "
                "Bạn vui lòng thử lại sau ít phút. "
                "Vui lòng không gửi mã PIN, OTP hoặc mật khẩu."
            ),
        )

    if not evidence.get("account_record_found"):
        # Case B: the logged-in account has NO lock/restriction record.
        # The claim ("tài khoản tôi bị khóa") is NOT evidence — say so honestly
        # and ask only for safe info (error time / screenshot).
        return ResolutionResult(
            resolution_status="no_match",
            resolved_entity_type="account",
            resolved_entity_id=user_id,
            public_safe_evidence=evidence,
            public_response=no_lock_evidence_message(),
            missing_info=["time_locked", "screenshot"],
        )

    # Case A (lock evidence) / Case C (active, no lock) — resolved from real
    # data → conclude, and do NOT keep asking for screenshots.
    return ResolutionResult(
        resolution_status="resolved",
        resolved_entity_type="account",
        resolved_entity_id=user_id,
        public_safe_evidence=evidence,
        verified_status=evidence.get("account_status", ""),
        verified_owner_id=user_id,
        missing_info=[],
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

    # wallet_topup (and default): reconciliation describes the bank side; the
    # CUSTOMER wallet-credit step is the transaction status itself.
    if wf in ("wallet_topup", "", "unknown") and txn_id:
        recon = _lookup_reconciliation(txn_id)
        if recon:
            evidence.update(recon)
        # Customer wallet credited only when the transaction itself succeeded.
        txn_status = str(getattr(txn, "status", "") or "").lower()
        if txn_status:
            evidence["wallet_status"] = (
                "received" if txn_status in ("success", "completed") else "not_received"
            )


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

        # NOTE: money_received_in_master_wallet means funds reached the SYSTEM /
        # master wallet — it is a payment-side fact, NOT proof the CUSTOMER's
        # wallet was credited. The customer wallet-credit signal is derived from
        # the transaction status in _enrich_evidence_for_workflow, so we do NOT
        # set wallet_status here (prevents a fake "ví đã nhận tiền" while the
        # customer's wallet is still pending).

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

    # ── Registry-based dispatch ──
    # Try the workflow registry for a custom resolver first.
    try:
        from fintech_agent.workflows.workflow_registry import get_registry
        registry = get_registry()
        spec = registry.get(workflow)
        if spec and spec.resolver is not None:
            logger.info(
                "[Resolver] Dispatching via registry: workflow=%s resolver=%s",
                workflow, spec.resolver.__name__,
            )
            return spec.resolver(
                session_context, extracted, workflow, message_analysis.message_type,
            )
    except Exception as exc:
        logger.warning(
            "[Resolver] Registry dispatch failed for '%s': %s — falling back",
            workflow, exc,
        )

    # ── Built-in dispatch (fallback for workflows without custom resolvers) ──
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
