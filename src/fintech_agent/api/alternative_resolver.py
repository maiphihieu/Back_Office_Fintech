"""Alternative transaction resolver — finds transactions when customer
provides amount, time, or bank name instead of transaction_id.

SECURITY INVARIANTS:
  - Only searches transactions belonging to the logged-in user (user_id from session).
  - NEVER returns user_id, wallet_id, or internal IDs to the frontend.
  - NEVER exposes reconciliation, ledger, fraud, or approval data.
  - Read-only: does not modify any transaction or balance.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fintech_agent.database.repository_factory import get_transaction_repo

logger = logging.getLogger(__name__)


# ─── Extracted Alternative Info ─────────────────────────────────

@dataclass
class ExtractedAltInfo:
    """Information extracted from customer's free-text message."""
    amount: int | None = None
    bank_name: str | None = None
    approximate_hour: int | None = None      # 0-23
    time_period: str | None = None           # "sáng nay", "hôm qua", etc.


@dataclass
class ResolverResult:
    """Result of alternative transaction search."""
    status: str  # "found" | "multiple" | "not_found" | "ownership_fail" | "error"
    transaction_id: str | None = None
    public_response: str = ""


# ─── Bank Name Normalization ───────────────────────────────────

_BANK_ALIASES: dict[str, str] = {
    "vietcombank": "VCB", "vcb": "VCB",
    "techcombank": "TCB", "tcb": "TCB",
    "mbbank": "MB", "mb": "MB",
    "bidv": "BIDV",
    "agribank": "AGRIBANK",
    "tpbank": "TPBANK",
    "vpbank": "VPBANK",
    "sacombank": "SACOMBANK",
    "acb": "ACB",
    "shb": "SHB",
    "vietinbank": "VIETINBANK", "ctg": "VIETINBANK",
    "hdbank": "HDBANK",
    "msb": "MSB",
    "ocb": "OCB",
}

_BANK_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _BANK_ALIASES.keys()) + r")\b",
    re.IGNORECASE,
)


def _normalize_bank(raw: str) -> str | None:
    """Normalize a bank alias to canonical short code."""
    return _BANK_ALIASES.get(raw.lower())


# ─── Amount Extraction ──────────────────────────────────────────

# Matches: 500000, 500.000, 500,000, 500k, 500 nghìn, 500 ngàn, 1tr, 1 triệu
# Branch 1: large plain numbers or separator-formatted (500.000, 500000)
_AMOUNT_LONG_RE = re.compile(
    r"(\d{1,3}(?:[.,]\d{3})+|\d{4,})"   # 500.000 / 500,000 / 500000
    r"\s*"
    r"(k|nghìn|ngàn|tr|triệu|đ|đồng|vnđ|vnd)?"
    r"(?!\d)",
    re.IGNORECASE,
)
# Branch 2: short numbers with mandatory suffix (500k, 1tr, 500 nghìn)
_AMOUNT_SHORT_RE = re.compile(
    r"(\d{1,3})"
    r"\s*"
    r"(k|nghìn|ngàn|tr|triệu)"
    r"(?!\w)",
    re.IGNORECASE,
)


def _extract_amount(message: str) -> int | None:
    """Extract monetary amount from Vietnamese message text."""
    # Try both branches: long-form first, then short-form with suffix
    for regex in (_AMOUNT_LONG_RE, _AMOUNT_SHORT_RE):
        for m in regex.finditer(message):
            raw_num = m.group(1).replace(".", "").replace(",", "")
            try:
                value = int(raw_num)
            except ValueError:
                continue

            suffix = (m.group(2) or "").lower()
            if suffix in ("k", "nghìn", "ngàn"):
                value *= 1000
            elif suffix in ("tr", "triệu"):
                value *= 1_000_000

            # Sanity: amounts < 1000 VND or > 1B VND are unlikely
            if 1000 <= value <= 1_000_000_000:
                return value

    return None


# ─── Time Extraction ────────────────────────────────────────────

_HOUR_RE = re.compile(
    r"(?:khoảng\s+)?(\d{1,2})\s*(?:h|g|giờ)\s*(sáng|chiều|tối)?",
    re.IGNORECASE,
)

_PERIOD_RE = re.compile(
    r"(sáng\s+nay|chiều\s+nay|tối\s+nay|tối\s+qua|hôm\s+nay|hôm\s+qua)",
    re.IGNORECASE,
)


def _extract_time(message: str) -> tuple[int | None, str | None]:
    """Extract approximate hour and time period from message.

    Returns (hour_0_23, period_str).
    """
    hour = None
    period = None

    hm = _HOUR_RE.search(message)
    if hm:
        h = int(hm.group(1))
        tod = (hm.group(2) or "").lower()
        if tod == "chiều" and h < 12:
            h += 12
        elif tod == "tối" and h < 18:
            h += 12
        if 0 <= h <= 23:
            hour = h

    pm = _PERIOD_RE.search(message)
    if pm:
        period = pm.group(1).lower()

    return hour, period


# ─── Bank Extraction ────────────────────────────────────────────

def _extract_bank(message: str) -> str | None:
    """Extract bank name from message."""
    m = _BANK_RE.search(message)
    if m:
        return _normalize_bank(m.group(1))
    return None


# ─── Public Entry Point ────────────────────────────────────────

def extract_alternative_info(message: str) -> ExtractedAltInfo:
    """Extract amount, bank, and time from a customer message."""
    amount = _extract_amount(message)
    bank = _extract_bank(message)
    hour, period = _extract_time(message)
    return ExtractedAltInfo(
        amount=amount,
        bank_name=bank,
        approximate_hour=hour,
        time_period=period,
    )


# ─── Response Templates ────────────────────────────────────────

_FOUND_RESPONSE = (
    "Chúng tôi đã tìm thấy giao dịch nạp tiền phù hợp với thông tin bạn cung cấp. "
    "Giao dịch đang được kiểm tra trạng thái giữa ngân hàng và ví. "
    "Nếu ngân hàng đã xác nhận trừ tiền nhưng ví chưa cập nhật, "
    "yêu cầu xử lý sẽ được chuyển cho bộ phận phụ trách. "
    "Chúng tôi sẽ thông báo khi có kết quả."
)

_FOUND_BANK_MISMATCH_RESPONSE = (
    "Chúng tôi đã ghi nhận giao dịch nạp tiền có dấu hiệu "
    "ngân hàng đã xử lý nhưng ví chưa cập nhật. "
    "Bộ phận phụ trách sẽ kiểm tra và xử lý theo quy trình. "
    "Bạn không cần cung cấp thêm mã PIN, OTP hoặc mật khẩu."
)

_MULTIPLE_RESPONSE = (
    "Tôi tìm thấy nhiều giao dịch có thông tin gần giống. "
    "Bạn vui lòng xác nhận thêm ngày giao dịch hoặc "
    "mã tham chiếu ngân hàng trên biên lai nếu có."
)

_NOT_FOUND_RESPONSE = (
    "Chúng tôi chưa tìm thấy giao dịch phù hợp với thông tin bạn cung cấp. "
    "Bạn vui lòng kiểm tra lại thời gian, số tiền hoặc gửi "
    "mã tham chiếu ngân hàng trên biên lai nếu có."
)

_OWNERSHIP_FAIL_RESPONSE = (
    "Chúng tôi chưa xác minh được giao dịch này thuộc tài khoản của bạn."
)

_SESSION_EXPIRED_RESPONSE = (
    "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại để tiếp tục hỗ trợ."
)


def resolve_transaction_from_alternatives(
    message: str,
    session: dict | None,
    active_case_id: str,
    selected_workflow: str,
    txn_repo=None,
) -> ResolverResult:
    """Resolve a transaction using alternative info (amount/time/bank).

    Args:
        message: Customer's free-text message.
        session: The authenticated session dict (from mock_customer_sessions).
        active_case_id: Current active case ID.
        selected_workflow: Current workflow (e.g. "wallet_topup").
        txn_repo: Optional injected transaction repo (for testing).

    Returns:
        ResolverResult with status and public-safe response.
    """
    if session is None:
        return ResolverResult(
            status="error",
            public_response=_SESSION_EXPIRED_RESPONSE,
        )

    user_id = session.get("user_id", "")
    if not user_id:
        return ResolverResult(
            status="error",
            public_response=_SESSION_EXPIRED_RESPONSE,
        )

    # Extract alternative info from message
    alt = extract_alternative_info(message)
    logger.info(
        "[AltResolver] Extracted: amount=%s, bank=%s, hour=%s, period=%s",
        alt.amount, alt.bank_name, alt.approximate_hour, alt.time_period,
    )

    # Get transaction repo
    if txn_repo is None:
        txn_repo = get_transaction_repo()

    # Fetch all transactions for the logged-in user
    try:
        user_txns = txn_repo.get_by_user_id(user_id)
    except Exception as exc:
        logger.error(
            "[AltResolver] Failed to fetch transactions for user: %s", exc,
        )
        return ResolverResult(
            status="error",
            public_response=_NOT_FOUND_RESPONSE,
        )

    if not user_txns:
        return ResolverResult(
            status="not_found",
            public_response=_NOT_FOUND_RESPONSE,
        )

    # Filter by workflow-relevant service types
    workflow_service_types = _get_service_types_for_workflow(selected_workflow)

    candidates = []
    for txn in user_txns:
        score = 0

        # Service type filter (required if available)
        if workflow_service_types:
            svc = getattr(txn, "service_type", "") or ""
            if svc.lower() not in workflow_service_types:
                continue

        # Amount match (strong signal)
        if alt.amount is not None:
            txn_amount = getattr(txn, "amount", 0) or 0
            if txn_amount == alt.amount:
                score += 3
            elif abs(txn_amount - alt.amount) <= alt.amount * 0.01:
                score += 2  # within 1%
            else:
                continue  # amount doesn't match at all — skip

        # Time match (medium signal)
        if alt.approximate_hour is not None:
            txn_time = getattr(txn, "created_at", None)
            if txn_time and isinstance(txn_time, datetime):
                if txn_time.hour == alt.approximate_hour:
                    score += 2
                elif abs(txn_time.hour - alt.approximate_hour) <= 1:
                    score += 1

        # Date filter (if period provided)
        if alt.time_period:
            txn_time = getattr(txn, "created_at", None)
            if txn_time and isinstance(txn_time, datetime):
                now = datetime.now(tz=txn_time.tzinfo or timezone.utc)
                if "hôm qua" in alt.time_period or "qua" in alt.time_period:
                    if txn_time.date() != (now - timedelta(days=1)).date():
                        continue
                elif "nay" in alt.time_period:
                    if txn_time.date() != now.date():
                        continue

        # If no amount/time criteria and we're just guessing, require recent
        if alt.amount is None and alt.approximate_hour is None and not alt.time_period:
            txn_time = getattr(txn, "created_at", None)
            if txn_time and isinstance(txn_time, datetime):
                now = datetime.now(tz=txn_time.tzinfo or timezone.utc)
                if (now - txn_time).days > 7:
                    continue

        if score > 0 or (alt.amount is None and alt.approximate_hour is None):
            candidates.append((score, txn))

    # Sort by score descending
    candidates.sort(key=lambda x: x[0], reverse=True)

    if not candidates:
        return ResolverResult(
            status="not_found",
            public_response=_NOT_FOUND_RESPONSE,
        )

    top_score = candidates[0][0]

    # Strong match: exactly one candidate with top score
    top_matches = [c for c in candidates if c[0] == top_score]

    if len(top_matches) == 1 and top_score >= 2:
        matched_txn = top_matches[0][1]
        txn_id = matched_txn.transaction_id

        # Verify ownership (defense in depth)
        if matched_txn.user_id != user_id:
            logger.warning(
                "[AltResolver] Ownership mismatch: txn=%s, expected=%s, got=%s",
                txn_id, user_id, matched_txn.user_id,
            )
            return ResolverResult(
                status="ownership_fail",
                public_response=_OWNERSHIP_FAIL_RESPONSE,
            )

        # Determine response based on transaction status
        txn_status = (getattr(matched_txn, "status", "") or "").lower()
        if txn_status in ("pending", "processing", "bank_success_wallet_pending"):
            response = _FOUND_BANK_MISMATCH_RESPONSE
        else:
            response = _FOUND_RESPONSE

        logger.info(
            "[AltResolver] Resolved txn_id=%s (score=%d, status=%s)",
            txn_id, top_score, txn_status,
        )
        return ResolverResult(
            status="found",
            transaction_id=txn_id,
            public_response=response,
        )

    elif len(top_matches) > 1:
        return ResolverResult(
            status="multiple",
            public_response=_MULTIPLE_RESPONSE,
        )

    else:
        # Low confidence single match
        if top_score >= 1:
            matched_txn = candidates[0][1]
            if matched_txn.user_id != user_id:
                return ResolverResult(
                    status="ownership_fail",
                    public_response=_OWNERSHIP_FAIL_RESPONSE,
                )
            txn_status = (getattr(matched_txn, "status", "") or "").lower()
            if txn_status in ("pending", "processing", "bank_success_wallet_pending"):
                response = _FOUND_BANK_MISMATCH_RESPONSE
            else:
                response = _FOUND_RESPONSE
            return ResolverResult(
                status="found",
                transaction_id=matched_txn.transaction_id,
                public_response=response,
            )

        return ResolverResult(
            status="not_found",
            public_response=_NOT_FOUND_RESPONSE,
        )


def _get_service_types_for_workflow(workflow: str) -> set[str]:
    """Map workflow to matching service_type values."""
    mapping: dict[str, set[str]] = {
        "wallet_topup": {"wallet_topup", "topup", "deposit"},
        "train_ticket": {"train_ticket", "train", "ticket"},
        "utility_bill": {"utility_bill", "bill_payment", "utility"},
        "merchant_settlement_delay": set(),  # merchant flows don't filter by service_type
    }
    return mapping.get(workflow, set())


def resolve_transaction_from_extracted(
    session: dict | None,
    active_case_id: str,
    selected_workflow: str,
    extracted_amount: int | None = None,
    extracted_bank: str | None = None,
    extracted_time_text: str | None = None,
    extracted_date_text: str | None = None,
    extracted_bank_ref: str | None = None,
    txn_repo=None,
) -> ResolverResult:
    """Resolve a transaction using pre-extracted fields from the LLM analyzer.

    This is the preferred entry point when using the LLM message analyzer,
    which already extracts amount/bank/time/reference from the message.

    Args:
        session: The authenticated session dict.
        active_case_id: Current active case ID.
        selected_workflow: Current workflow.
        extracted_amount: Amount in VND (already parsed by LLM/fallback).
        extracted_bank: Bank name (already normalized by LLM/fallback).
        extracted_time_text: Approximate time text (e.g. "9h sáng").
        extracted_date_text: Approximate date text (e.g. "sáng nay").
        extracted_bank_ref: Bank reference code.
        txn_repo: Optional injected transaction repo (for testing).

    Returns:
        ResolverResult with status and public-safe response.
    """
    if session is None:
        return ResolverResult(
            status="error",
            public_response=_SESSION_EXPIRED_RESPONSE,
        )

    user_id = session.get("user_id", "")
    if not user_id:
        return ResolverResult(
            status="error",
            public_response=_SESSION_EXPIRED_RESPONSE,
        )

    # Normalize bank name through aliases
    norm_bank = None
    if extracted_bank:
        norm_bank = _normalize_bank(extracted_bank) or extracted_bank.upper()

    # Parse approximate_hour from time text
    approximate_hour = None
    if extracted_time_text:
        hour_match = re.search(r"(\d{1,2})\s*(?:h|g|giờ)", extracted_time_text, re.IGNORECASE)
        if hour_match:
            h = int(hour_match.group(1))
            if "chiều" in extracted_time_text.lower() and h < 12:
                h += 12
            elif "tối" in extracted_time_text.lower() and h < 18:
                h += 12
            if 0 <= h <= 23:
                approximate_hour = h

    # Parse time_period from date text
    time_period = None
    if extracted_date_text:
        time_period = extracted_date_text.lower().strip()

    # Build ExtractedAltInfo from pre-extracted fields
    alt = ExtractedAltInfo(
        amount=extracted_amount,
        bank_name=norm_bank,
        approximate_hour=approximate_hour,
        time_period=time_period,
    )

    logger.info(
        "[AltResolver] From extracted: amount=%s, bank=%s, hour=%s, period=%s",
        alt.amount, alt.bank_name, alt.approximate_hour, alt.time_period,
    )

    # Get transaction repo
    if txn_repo is None:
        txn_repo = get_transaction_repo()

    # Reuse the core search logic
    return _search_and_rank_transactions(
        user_id=user_id,
        alt=alt,
        selected_workflow=selected_workflow,
        txn_repo=txn_repo,
    )


def _search_and_rank_transactions(
    user_id: str,
    alt: ExtractedAltInfo,
    selected_workflow: str,
    txn_repo,
) -> ResolverResult:
    """Core transaction search and ranking logic.

    Shared between resolve_transaction_from_alternatives (regex path)
    and resolve_transaction_from_extracted (LLM path).
    """
    # Fetch all transactions for the logged-in user
    try:
        user_txns = txn_repo.get_by_user_id(user_id)
    except Exception as exc:
        logger.error(
            "[AltResolver] Failed to fetch transactions for user: %s", exc,
        )
        return ResolverResult(
            status="error",
            public_response=_NOT_FOUND_RESPONSE,
        )

    if not user_txns:
        return ResolverResult(
            status="not_found",
            public_response=_NOT_FOUND_RESPONSE,
        )

    # Filter by workflow-relevant service types
    workflow_service_types = _get_service_types_for_workflow(selected_workflow)

    candidates = []
    for txn in user_txns:
        score = 0

        # Service type filter (required if available)
        if workflow_service_types:
            svc = getattr(txn, "service_type", "") or ""
            if svc.lower() not in workflow_service_types:
                continue

        # Amount match (strong signal)
        if alt.amount is not None:
            txn_amount = getattr(txn, "amount", 0) or 0
            if txn_amount == alt.amount:
                score += 3
            elif abs(txn_amount - alt.amount) <= alt.amount * 0.01:
                score += 2  # within 1%
            else:
                continue  # amount doesn't match at all — skip

        # Time match (medium signal)
        if alt.approximate_hour is not None:
            txn_time = getattr(txn, "created_at", None)
            if txn_time and isinstance(txn_time, datetime):
                if txn_time.hour == alt.approximate_hour:
                    score += 2
                elif abs(txn_time.hour - alt.approximate_hour) <= 1:
                    score += 1

        # Date filter (if period provided)
        if alt.time_period:
            txn_time = getattr(txn, "created_at", None)
            if txn_time and isinstance(txn_time, datetime):
                now = datetime.now(tz=txn_time.tzinfo or timezone.utc)
                if "hôm qua" in alt.time_period or "qua" in alt.time_period:
                    if txn_time.date() != (now - timedelta(days=1)).date():
                        continue
                elif "nay" in alt.time_period:
                    if txn_time.date() != now.date():
                        continue

        # If no amount/time criteria and we're just guessing, require recent
        if alt.amount is None and alt.approximate_hour is None and not alt.time_period:
            txn_time = getattr(txn, "created_at", None)
            if txn_time and isinstance(txn_time, datetime):
                now = datetime.now(tz=txn_time.tzinfo or timezone.utc)
                if (now - txn_time).days > 7:
                    continue

        if score > 0 or (alt.amount is None and alt.approximate_hour is None):
            candidates.append((score, txn))

    # Sort by score descending
    candidates.sort(key=lambda x: x[0], reverse=True)

    if not candidates:
        return ResolverResult(
            status="not_found",
            public_response=_NOT_FOUND_RESPONSE,
        )

    top_score = candidates[0][0]

    # Strong match: exactly one candidate with top score
    top_matches = [c for c in candidates if c[0] == top_score]

    if len(top_matches) == 1 and top_score >= 2:
        matched_txn = top_matches[0][1]
        return _build_match_result(matched_txn, user_id, top_score)

    elif len(top_matches) > 1:
        return ResolverResult(
            status="multiple",
            public_response=_MULTIPLE_RESPONSE,
        )

    else:
        # Low confidence single match
        if top_score >= 1:
            matched_txn = candidates[0][1]
            return _build_match_result(matched_txn, user_id, top_score)

        return ResolverResult(
            status="not_found",
            public_response=_NOT_FOUND_RESPONSE,
        )


def _build_match_result(
    matched_txn, user_id: str, score: int,
) -> ResolverResult:
    """Build ResolverResult from a matched transaction, with ownership check."""
    txn_id = matched_txn.transaction_id

    # Verify ownership (defense in depth)
    if matched_txn.user_id != user_id:
        logger.warning(
            "[AltResolver] Ownership mismatch: txn=%s, expected=%s, got=%s",
            txn_id, user_id, matched_txn.user_id,
        )
        return ResolverResult(
            status="ownership_fail",
            public_response=_OWNERSHIP_FAIL_RESPONSE,
        )

    # Determine response based on transaction status
    txn_status = (getattr(matched_txn, "status", "") or "").lower()
    if txn_status in ("pending", "processing", "bank_success_wallet_pending"):
        response = _FOUND_BANK_MISMATCH_RESPONSE
    else:
        response = _FOUND_RESPONSE

    logger.info(
        "[AltResolver] Resolved txn_id=%s (score=%d, status=%s)",
        txn_id, score, txn_status,
    )
    return ResolverResult(
        status="found",
        transaction_id=txn_id,
        public_response=response,
    )

