"""Mock/regex extractor — the MVP extraction logic.

This is the deterministic regex-based extractor that was the original
extract_info logic. Used when MOCK_LLM=true (default) or as fallback
when OpenAI extraction fails.

Phase 3 addition: extract_claims() provides generic, semantically-typed
claim extraction. Numbers are classified by context, not assumed to be
transaction amounts.

IMPORTANT: This must produce identical results to the old inline regex
in nodes/extract_info.py to avoid regression on ExtractedInfo fields.
"""

from __future__ import annotations

import re

from fintech_agent.schemas.case_state import ExtractedInfo
from fintech_agent.schemas.claim_verification import Claim, ClaimType


# ─── Semantic number context patterns ────────────────────────
# Each tuple: (compiled_regex, ClaimType)
# Order matters — first match wins for the *context around* the number.

_AMOUNT_CONTEXT_PATTERNS: list[tuple[re.Pattern, ClaimType]] = [
    # Wallet balance: "ví vẫn 0đ", "ví vẫn báo 0đ", "ví hiển thị 0đ",
    # "số dư 0", "balance 0", "ví còn 0đ", "ví vẫn hiển thị 0đ"
    # The regex handles compound modifiers: vẫn + báo, vẫn + hiển thị, etc.
    (re.compile(
        r"(?:"
        r"ví\s*(?:vẫn\s*)?(?:báo|hiện|còn|hiển\s*thị)"  # "ví vẫn báo", "ví báo", "ví hiển thị", "ví vẫn hiển thị"
        r"|ví\s*(?:vẫn|còn)"                               # "ví vẫn", "ví còn" (standalone)
        r"|số\s*dư(?:\s*ví)?"                              # "số dư", "số dư ví"
        r"|balance|remaining"
        r")"
        r"\s*(?:là\s*|=\s*|:?\s*)"
        r"(\d[\d,.]*)\s*(?:VND|₫|đồng|dong|đ)?",
        re.IGNORECASE,
    ), ClaimType.WALLET_BALANCE),

    # Transaction amount: "nạp 500.000đ", "thanh toán 450000", "trừ 200.000",
    # "debit 300.000", "chuyển 100.000"
    (re.compile(
        r"(?:nạp|thanh\s*toán|trừ|debit|chuyển|charged?|paid?|trả)"
        r"\s+(\d[\d,.]*)\s*(?:VND|₫|đồng|dong|đ)?",
        re.IGNORECASE,
    ), ClaimType.TRANSACTION_AMOUNT),

    # Amount with "tiền" nearby: "số tiền 500.000đ", "tiền 450.000 VND"
    (re.compile(
        r"(?:số\s*)?tiền\s*(?:là\s*|=\s*|:?\s*)(\d[\d,.]*)\s*(?:VND|₫|đồng|dong|đ)?",
        re.IGNORECASE,
    ), ClaimType.TRANSACTION_AMOUNT),
]

# Wallet-context exclusion regex (used to prevent catch-all from
# misclassifying wallet-balance numbers as transaction amounts).
_WALLET_CONTEXT_RE = re.compile(
    r"(?:"
    r"ví\s*(?:vẫn\s*)?(?:báo|hiện|còn|hiển\s*thị)"
    r"|ví\s*(?:vẫn|còn)"
    r"|số\s*dư(?:\s*ví)?"
    r"|balance|remaining"
    r")"
    r"\s*(?:là\s*|=\s*|:?\s*)"
    r"\d[\d,.]*\s*(?:VND|₫|đồng|dong|đ)?",
    re.IGNORECASE,
)

# Payment status keywords
_PAYMENT_STATUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?:ngân\s*hàng|bank)\s*(?:đã\s*)?trừ\s*tiền", re.IGNORECASE), "bank_deducted"),
    (re.compile(r"(?:đã\s*)?thanh\s*toán\s*(?:thành\s*công|rồi)", re.IGNORECASE), "payment_completed"),
    (re.compile(r"(?:đã\s*)?trừ\s*tiền", re.IGNORECASE), "money_deducted"),
    (re.compile(r"chưa\s*nhận\s*(?:được\s*)?tiền", re.IGNORECASE), "money_not_received"),
]

# Service delivery keywords
_SERVICE_DELIVERY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"chưa\s*nhận\s*(?:được\s*)?vé", re.IGNORECASE), "ticket_not_received"),
    (re.compile(r"không\s*nhận\s*(?:được\s*)?vé", re.IGNORECASE), "ticket_not_received"),
    (re.compile(r"chưa\s*có\s*vé", re.IGNORECASE), "ticket_not_received"),
    (re.compile(r"chưa\s*(?:thanh\s*toán|xác\s*nhận)\s*(?:hóa\s*đơn|bill)", re.IGNORECASE), "bill_not_confirmed"),
    (re.compile(r"hóa\s*đơn.*chưa\s*(?:thanh\s*toán|xác\s*nhận)", re.IGNORECASE), "bill_not_confirmed"),
]

# Account status keywords
_ACCOUNT_STATUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?:tài\s*khoản|account)\s*(?:bị\s*)?(?:khóa|lock)", re.IGNORECASE), "account_locked"),
    (re.compile(r"(?:bị\s*khóa|locked)\s*(?:tài\s*khoản|account)?", re.IGNORECASE), "account_locked"),
    (re.compile(r"không\s*(?:thể\s*)?rút\s*tiền", re.IGNORECASE), "withdrawal_blocked"),
]

# Refund status keywords
_REFUND_STATUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"chưa\s*(?:nhận\s*(?:được\s*)?)?hoàn\s*tiền", re.IGNORECASE), "refund_not_received"),
    (re.compile(r"hoàn\s*tiền.*chưa", re.IGNORECASE), "refund_not_received"),
    (re.compile(r"(?:yêu\s*cầu|request)\s*hoàn\s*tiền", re.IGNORECASE), "refund_requested"),
]


def _parse_number(raw: str) -> int | None:
    """Parse a number string, stripping thousands separators."""
    cleaned = raw.replace(",", "").replace(".", "")
    try:
        return int(cleaned)
    except ValueError:
        return None


def extract_claims(complaint: str) -> list[Claim]:
    """Extract generic, semantically-typed claims from complaint text.

    Key design:
    - Numbers are classified by surrounding context (wallet balance vs
      transaction amount vs identifier).
    - Transaction ID patterns are never treated as amounts.
    - Unclear numbers become UNKNOWN, not TRANSACTION_AMOUNT.
    - Status assertions (payment, service delivery, account) are detected
      by keyword patterns.

    Returns:
        List of Claim objects with claim_type, customer_claimed_value,
        raw_text, and confidence set. Verification fields are NOT set
        here — that happens in claim_verifier.py.
    """
    claims: list[Claim] = []
    seen_claim_types: set[str] = set()

    # ── 1. Transaction ID claims ──────────────────────────────
    txn_matches = re.finditer(r"(TXN[\-_]\w+)", complaint)
    for m in txn_matches:
        txn_id = m.group(1)
        if ClaimType.TRANSACTION_ID not in seen_claim_types:
            claims.append(Claim(
                claim_type=ClaimType.TRANSACTION_ID,
                raw_text=txn_id,
                customer_claimed_value=txn_id,
                normalized_value=txn_id,
                unit="identifier",
                confidence=1.0,
            ))
            seen_claim_types.add(ClaimType.TRANSACTION_ID)

    # ── 2. Semantic number classification ─────────────────────
    for pattern, claim_type in _AMOUNT_CONTEXT_PATTERNS:
        for m in pattern.finditer(complaint):
            if claim_type in seen_claim_types:
                continue
            raw_num = m.group(1)
            parsed = _parse_number(raw_num)
            if parsed is not None:
                claims.append(Claim(
                    claim_type=claim_type,
                    raw_text=m.group(0).strip(),
                    customer_claimed_value=parsed,
                    normalized_value=parsed,
                    unit="VND",
                    confidence=0.9,
                ))
                seen_claim_types.add(claim_type)

    # ── 3. Payment status claims ──────────────────────────────
    for pattern, status_value in _PAYMENT_STATUS_PATTERNS:
        m = pattern.search(complaint)
        if m and ClaimType.PAYMENT_STATUS not in seen_claim_types:
            claims.append(Claim(
                claim_type=ClaimType.PAYMENT_STATUS,
                raw_text=m.group(0).strip(),
                customer_claimed_value=status_value,
                normalized_value=status_value,
                unit="status",
                confidence=0.85,
            ))
            seen_claim_types.add(ClaimType.PAYMENT_STATUS)
            break

    # ── 4. Service delivery claims ────────────────────────────
    for pattern, delivery_value in _SERVICE_DELIVERY_PATTERNS:
        m = pattern.search(complaint)
        if m and ClaimType.SERVICE_DELIVERY not in seen_claim_types:
            claims.append(Claim(
                claim_type=ClaimType.SERVICE_DELIVERY,
                raw_text=m.group(0).strip(),
                customer_claimed_value=delivery_value,
                normalized_value=delivery_value,
                unit="status",
                confidence=0.85,
            ))
            seen_claim_types.add(ClaimType.SERVICE_DELIVERY)
            break

    # ── 5. Account status claims ──────────────────────────────
    for pattern, acct_value in _ACCOUNT_STATUS_PATTERNS:
        m = pattern.search(complaint)
        if m and ClaimType.ACCOUNT_STATUS not in seen_claim_types:
            claims.append(Claim(
                claim_type=ClaimType.ACCOUNT_STATUS,
                raw_text=m.group(0).strip(),
                customer_claimed_value=acct_value,
                normalized_value=acct_value,
                unit="status",
                confidence=0.85,
            ))
            seen_claim_types.add(ClaimType.ACCOUNT_STATUS)
            break

    # ── 6. Refund status claims ───────────────────────────────
    for pattern, refund_value in _REFUND_STATUS_PATTERNS:
        m = pattern.search(complaint)
        if m and ClaimType.REFUND_STATUS not in seen_claim_types:
            claims.append(Claim(
                claim_type=ClaimType.REFUND_STATUS,
                raw_text=m.group(0).strip(),
                customer_claimed_value=refund_value,
                normalized_value=refund_value,
                unit="status",
                confidence=0.85,
            ))
            seen_claim_types.add(ClaimType.REFUND_STATUS)
            break

    return claims


def mock_extract(complaint: str, user_id: str | None = None) -> ExtractedInfo:
    """Extract structured info from complaint using regex/rules.

    This is the MVP extractor. It detects transaction IDs, user IDs,
    and service types from known patterns in the complaint text.

    Phase 3: Also produces generic claims via extract_claims().

    Args:
        complaint: Raw complaint text.
        user_id: Pre-supplied user_id (from state), takes priority over regex.

    Returns:
        ExtractedInfo with fields populated from regex matches + claims.
    """
    # Extract transaction_id
    txn_id: str | None = None
    m = re.search(r"(TXN[\-_]\w+)", complaint)
    if m:
        txn_id = m.group(1)

    # Extract user_id (use provided or regex)
    extracted_user_id = user_id
    if not extracted_user_id:
        # Try U_FRAUD_xxx pattern first (fraud use case)
        m = re.search(r"(U_FRAUD_\w+)", complaint)
        if m:
            extracted_user_id = m.group(1)
        else:
            # Fall back to generic U### pattern
            m = re.search(r"(U\d{3})", complaint)
            extracted_user_id = m.group(1) if m else None

    # ── Extract phone number ──────────────────────────────────
    # Vietnamese mobile: 09x, 08x, 07x, 03x, 05x (10 digits)
    extracted_phone: str | None = None
    m = re.search(r"(?:^|\s|[:(])?(0(?:9|8|7|3|5)\d{8})(?:\s|$|[,.)\-])", complaint)
    if m:
        extracted_phone = m.group(1)

    # ── Extract email ─────────────────────────────────────────
    extracted_email: str | None = None
    m = re.search(r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})", complaint)
    if m:
        extracted_email = m.group(1)

    # ── Extract wallet_id ─────────────────────────────────────
    extracted_wallet_id: str | None = None
    m = re.search(r"(WALLET_\w+)", complaint)
    if m:
        extracted_wallet_id = m.group(1)

    # Detect service_type from txn_id prefix or keywords
    service_type: str | None = None
    if txn_id:
        if any(txn_id.startswith(prefix) for prefix in ("TXN_TRAIN", "TXN_CONFLICT", "TXN_REFUND", "TXN-")):
            # TXN- prefix used in demo script (e.g. TXN-20260527-001)
            # For demo script TXN- IDs, we fall through to keyword detection
            if not txn_id.startswith("TXN-"):
                service_type = "train_ticket"
        if txn_id.startswith("TXN_BILL"):
            service_type = "electric_bill"
        if txn_id.startswith("TXN_TOPUP"):
            service_type = "wallet_topup"

    if not service_type:
        lower = complaint.lower()
        if any(kw in lower for kw in ("vé tàu", "train", "ticket")):
            service_type = "train_ticket"
        # account_security MUST come before electric_bill because
        # "điện thoại" (phone) contains "điện" (electricity).
        elif any(kw in lower for kw in (
            "tài khoản bị khóa", "bị khóa vô cớ", "không thể rút tiền",
            "khóa tài khoản", "rút tiền", "account locked", "bị khóa",
        )):
            service_type = "account_security"
        elif any(kw in lower for kw in (
            "tiền điện", "hóa đơn điện", "điện lực", "electric",
        )):
            service_type = "electric_bill"
        elif any(kw in lower for kw in ("nước", "water")):
            service_type = "water_bill"
        elif any(kw in lower for kw in (
            "nạp tiền", "ngân hàng", "bank đã trừ", "ví vẫn 0",
            "ví báo 0", "topup", "top-up", "nạp ví",
        )):
            service_type = "wallet_topup"

    # Detect issue_type from keywords
    issue_type: str | None = None
    lower = complaint.lower()
    if service_type == "wallet_topup":
        issue_type = "topup_pending"
    elif service_type == "account_security":
        issue_type = "account_locked"
        # NOTE: Do NOT default to U_FRAUD_001. If user_id is missing,
        # identity will be resolved from phone/email/wallet_id in fetch_evidence.
        # Defaulting to a hardcoded user_id could fetch the WRONG user's data.
    elif any(kw in lower for kw in ("chưa nhận", "không nhận", "no ticket")):
        issue_type = "paid_but_no_ticket"
    elif any(kw in lower for kw in ("chưa xác nhận", "not confirmed")):
        issue_type = "paid_but_provider_not_confirmed"
    elif any(kw in lower for kw in ("thất bại", "bị lỗi", "failed")):
        issue_type = "provider_failed"

    # Extract amount_claimed from text (e.g. "350,000 VND", "450000₫")
    # SAFETY: Skip numbers that are in wallet-balance context (e.g. "ví vẫn 0đ")
    # Those describe the balance the customer SEES, not the transaction amount.
    amount_claimed: int | None = None

    # First, find all wallet-balance context spans to exclude
    # (uses module-level _WALLET_CONTEXT_RE with compound modifier support)
    wallet_spans: list[tuple[int, int]] = [
        (wm.start(), wm.end()) for wm in _WALLET_CONTEXT_RE.finditer(complaint)
    ]

    # Only use explicit transaction amount patterns for amount_claimed
    _txn_amount_re = re.compile(
        r"(?:nạp|thanh\s*toán|trừ|debit|chuyển|charged?|paid?|trả)"
        r"\s+(\d[\d,.]*)\s*(?:VND|₫|đồng|dong|đ)?",
        re.IGNORECASE,
    )
    _money_label_re = re.compile(
        r"(?:số\s*)?tiền\s*(?:là\s*|=\s*|:?\s*)(\d[\d,.]*)\s*(?:VND|₫|đồng|dong|đ)?",
        re.IGNORECASE,
    )

    for pattern in [_txn_amount_re, _money_label_re]:
        m = pattern.search(complaint)
        if m:
            # Check if this match falls inside a wallet-balance context span
            in_wallet_ctx = any(ws <= m.start() < we for ws, we in wallet_spans)
            if not in_wallet_ctx:
                raw = m.group(1).replace(",", "").replace(".", "")
                try:
                    amount_claimed = int(raw)
                except ValueError:
                    pass
                break

    # Compute missing fields
    # Note: transaction_id is NOT required for account_security workflow
    missing: list[str] = []
    if not txn_id and service_type != "account_security":
        missing.append("transaction_id")
    if not extracted_user_id:
        # For fraud, phone/email/wallet_id can resolve user_id later
        has_identity_hint = (
            extracted_phone or extracted_email or extracted_wallet_id
        )
        if not has_identity_hint:
            missing.append("user_id")
    if not service_type:
        missing.append("service_type")

    # ── Extract generic claims ────────────────────────────────
    generic_claims = extract_claims(complaint)

    return ExtractedInfo(
        transaction_id=txn_id,
        user_id=extracted_user_id,
        service_type=service_type,
        issue_type=issue_type,
        order_id=None,
        bill_code=None,
        customer_code=None,
        phone=extracted_phone,
        email=extracted_email,
        wallet_id=extracted_wallet_id,
        amount_claimed=amount_claimed,
        language="vi",
        confidence=1.0 if not missing else 0.5,
        extraction_method="mock_regex",
        missing_fields=missing,
        claims=generic_claims,
    )
