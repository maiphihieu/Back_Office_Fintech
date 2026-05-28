"""Mock/regex extractor — the MVP extraction logic.

This is the deterministic regex-based extractor that was the original
extract_info logic. Used when MOCK_LLM=true (default) or as fallback
when OpenAI extraction fails.

IMPORTANT: This must produce identical results to the old inline regex
in nodes/extract_info.py to avoid regression.
"""

from __future__ import annotations

import re

from fintech_agent.schemas.case_state import ExtractedInfo


def mock_extract(complaint: str, user_id: str | None = None) -> ExtractedInfo:
    """Extract structured info from complaint using regex/rules.

    This is the MVP extractor. It detects transaction IDs, user IDs,
    and service types from known patterns in the complaint text.

    Args:
        complaint: Raw complaint text.
        user_id: Pre-supplied user_id (from state), takes priority over regex.

    Returns:
        ExtractedInfo with fields populated from regex matches.
    """
    # Extract transaction_id
    txn_id: str | None = None
    m = re.search(r"(TXN[\-_]\w+)", complaint)
    if m:
        txn_id = m.group(1)

    # Extract user_id (use provided or regex)
    extracted_user_id = user_id
    if not extracted_user_id:
        m = re.search(r"(U\d{3})", complaint)
        extracted_user_id = m.group(1) if m else None

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

    if not service_type:
        lower = complaint.lower()
        if any(kw in lower for kw in ("vé tàu", "train", "ticket")):
            service_type = "train_ticket"
        elif any(kw in lower for kw in ("điện", "electric")):
            service_type = "electric_bill"
        elif any(kw in lower for kw in ("nước", "water")):
            service_type = "water_bill"

    # Detect issue_type from keywords
    issue_type: str | None = None
    lower = complaint.lower()
    if any(kw in lower for kw in ("chưa nhận", "không nhận", "no ticket")):
        issue_type = "paid_but_no_ticket"
    elif any(kw in lower for kw in ("chưa xác nhận", "not confirmed")):
        issue_type = "paid_but_provider_not_confirmed"
    elif any(kw in lower for kw in ("thất bại", "bị lỗi", "failed")):
        issue_type = "provider_failed"

    # Extract amount_claimed from text (e.g. "350,000 VND", "450000₫")
    amount_claimed: int | None = None
    m = re.search(r"(\d[\d,.]*)\s*(?:VND|₫|đồng|dong)", complaint, re.IGNORECASE)
    if m:
        raw = m.group(1).replace(",", "").replace(".", "")
        try:
            amount_claimed = int(raw)
        except ValueError:
            pass

    # Compute missing fields
    missing: list[str] = []
    if not txn_id:
        missing.append("transaction_id")
    if not extracted_user_id:
        missing.append("user_id")
    if not service_type:
        missing.append("service_type")

    return ExtractedInfo(
        transaction_id=txn_id,
        user_id=extracted_user_id,
        service_type=service_type,
        issue_type=issue_type,
        order_id=None,
        bill_code=None,
        customer_code=None,
        amount_claimed=amount_claimed,
        language="vi",
        confidence=1.0 if not missing else 0.5,
        extraction_method="mock_regex",
        missing_fields=missing,
    )
