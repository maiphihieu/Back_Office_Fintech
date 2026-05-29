"""Wallet topup message templates — maps diagnosis codes to human-readable messages.

Each diagnosis code from wallet_topup_rules.py is mapped to:
  - cs_message: Message for CS/Ops staff (internal)
  - customer_message: Message for the customer (customer-facing)

These messages do NOT change any rule logic, risk level, or approval behavior.
They only improve the readability of draft outputs for CSKH and Ops.
"""

from __future__ import annotations

from fintech_agent.schemas.enums import ActionType


# ─── Internal (CS/Ops) messages by action type ──────────────

FORCE_SUCCESS_CS_MESSAGE = (
    "Ngân hàng đã xác nhận giao dịch thành công và tiền đã vào tài khoản tổng "
    "của ví, nhưng giao dịch ví vẫn đang ở trạng thái pending. "
    "Hệ thống đã tạo draft Force-Success và cần nhân viên phê duyệt "
    "trước khi cập nhật trạng thái/cộng tiền."
)

MANUAL_REVIEW_CS_MESSAGE = (
    "Dữ liệu đối soát chưa đủ hoặc đang mâu thuẫn. "
    "Không được Force-Success tự động. "
    "Cần chuyển manual review để Ops kiểm tra reconciliation file/log giao dịch."
)


# ─── Customer-facing messages by diagnosis ───────────────────

BANK_FAILED_CUSTOMER_MESSAGE = (
    "Ngân hàng chưa xác nhận giao dịch thành công hoặc tiền chưa vào "
    "tài khoản tổng của ví. Vui lòng thông báo khách chờ ngân hàng "
    "hoàn tiền trong 3–5 ngày làm việc."
)


# ─── Diagnosis → customer message mapping ────────────────────

_CUSTOMER_MESSAGE_MAP: dict[str, str] = {
    # Case B: bank failed / rejected
    "bank_transfer_failed_wait_reversal": BANK_FAILED_CUSTOMER_MESSAGE,
    # Case B variant: money not received in master wallet
    "money_not_received_in_master_wallet": BANK_FAILED_CUSTOMER_MESSAGE,
    # Transaction not pending (already processed)
    "transaction_not_pending": (
        "Giao dịch của bạn đã được xử lý. "
        "Nếu vẫn chưa nhận được tiền, vui lòng liên hệ lại để được hỗ trợ."
    ),
}


# ─── Public API ──────────────────────────────────────────────


def get_customer_message(diagnosis: str) -> str:
    """Get customer-facing message from diagnosis code.

    Falls back to a generic message if the diagnosis code is not mapped.

    Args:
        diagnosis: The diagnosis code from rule engine (e.g. "bank_transfer_failed_wait_reversal").

    Returns:
        Customer-friendly message string.
    """
    # Check exact match first
    if diagnosis in _CUSTOMER_MESSAGE_MAP:
        return _CUSTOMER_MESSAGE_MAP[diagnosis]

    # Check prefix match (e.g. "transaction_not_pending (status=completed)")
    for key, msg in _CUSTOMER_MESSAGE_MAP.items():
        if diagnosis.startswith(key):
            return msg

    # Fallback
    return f"Kết quả kiểm tra: {diagnosis}"


def get_cs_message(action_type: ActionType, diagnosis: str) -> str:
    """Get internal CS/Ops message for a wallet_topup action.

    Args:
        action_type: The recommended action type.
        diagnosis: The diagnosis code from rule engine.

    Returns:
        Internal message for CS/Ops staff.
    """
    if action_type == ActionType.CREATE_FORCE_SUCCESS_DRAFT:
        return FORCE_SUCCESS_CS_MESSAGE

    if action_type == ActionType.MANUAL_REVIEW:
        return MANUAL_REVIEW_CS_MESSAGE

    if action_type == ActionType.DRAFT_CUSTOMER_RESPONSE:
        return get_customer_message(diagnosis)

    return f"Kết quả kiểm tra: {diagnosis}"
