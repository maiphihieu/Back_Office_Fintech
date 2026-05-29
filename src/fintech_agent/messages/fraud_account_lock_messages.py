"""Message templates for fraud/account lock use case.

Maps internal diagnosis codes to human-readable messages for:
- CS/Ops staff (cs_message): Vietnamese, professional internal language
- Customer-facing (customer_message): Vietnamese, empathetic, clear instructions
"""

from __future__ import annotations

from fintech_agent.schemas.enums import ActionType

# ═══════════════════════════════════════════════════════════════
#  CS/Ops messages (internal — shown in Back Office dashboard)
# ═══════════════════════════════════════════════════════════════

_CS_MESSAGES: dict[str, dict[str, str]] = {
    ActionType.CREATE_UNLOCK_ACCOUNT_DRAFT: {
        "likely_false_positive_unlock_candidate": (
            "Hệ thống Fraud Detection có khả năng khóa nhầm. "
            "Không phát hiện dấu hiệu gian lận nghiêm trọng. "
            "Đề xuất tạo draft mở khóa tài khoản, cần Risk/Fraud phê duyệt."
        ),
    },
    ActionType.CREATE_REQUEST_DOCUMENTS_RESPONSE_DRAFT: {
        "suspicious_activity_keep_locked_request_documents": (
            "Tài khoản có nhiều dấu hiệu rủi ro cao (risk_score >= 70 hoặc "
            "có signal nguy hiểm). Giữ nguyên trạng thái khóa và yêu cầu "
            "khách hàng bổ sung giấy tờ xác minh. Chuyển Risk/Fraud review."
        ),
    },
    ActionType.MANUAL_REVIEW: {
        "missing_account_or_fraud_evidence": (
            "Thiếu dữ liệu account hoặc fraud case. "
            "Không thể đánh giá tự động. Chuyển manual review."
        ),
        "fraud_review_inconclusive": (
            "Risk score ở mức trung bình (50-69), không đủ điều kiện "
            "tự động quyết định. Chuyển Risk/Fraud manual review."
        ),
        "conflict_detected": (
            "Phát hiện mâu thuẫn dữ liệu giữa các nguồn. "
            "Chuyển manual review để kiểm tra thủ công."
        ),
    },
    ActionType.DRAFT_CUSTOMER_RESPONSE: {
        "account_not_locked": (
            "Tài khoản không ở trạng thái khóa. "
            "Thông báo cho khách hàng tài khoản đang hoạt động bình thường."
        ),
    },
}

# ═══════════════════════════════════════════════════════════════
#  Customer-facing messages
# ═══════════════════════════════════════════════════════════════

_CUSTOMER_MESSAGES: dict[str, str] = {
    "likely_false_positive_unlock_candidate": (
        "Chúng tôi đã kiểm tra và nhận thấy tài khoản của bạn có thể đã bị "
        "khóa nhầm. Chúng tôi đang tiến hành xử lý mở khóa tài khoản. "
        "Vui lòng chờ trong vòng 24 giờ. Nếu có thắc mắc, xin liên hệ hotline."
    ),
    "suspicious_activity_keep_locked_request_documents": (
        "Tài khoản đang được tạm khóa do hệ thống phát hiện dấu hiệu bất thường. "
        "Vui lòng bổ sung giấy tờ/chứng minh giao dịch để bộ phận Risk/Fraud kiểm tra. "
        "Trong thời gian xác minh, chức năng rút tiền sẽ tạm thời bị hạn chế."
    ),
    "account_not_locked": (
        "Tài khoản của bạn hiện đang hoạt động bình thường. "
        "Nếu bạn gặp vấn đề khi sử dụng, vui lòng liên hệ hotline để được hỗ trợ."
    ),
    "missing_account_or_fraud_evidence": (
        "Chúng tôi đang kiểm tra tài khoản của bạn. "
        "Bộ phận hỗ trợ sẽ liên hệ lại trong thời gian sớm nhất."
    ),
    "fraud_review_inconclusive": (
        "Tài khoản của bạn đang được bộ phận an ninh kiểm tra. "
        "Chúng tôi sẽ thông báo kết quả trong vòng 24-48 giờ."
    ),
    "conflict_detected": (
        "Chúng tôi đang kiểm tra tài khoản của bạn. "
        "Bộ phận hỗ trợ sẽ liên hệ lại trong thời gian sớm nhất."
    ),
}


def get_cs_message(action_type: ActionType, diagnosis: str) -> str:
    """Get CS/Ops-facing message for a fraud/account lock diagnosis.

    Args:
        action_type: The recommended action type.
        diagnosis: The internal diagnosis code.

    Returns:
        Human-readable message for CS staff. Falls back to diagnosis code
        if no mapping exists.
    """
    action_msgs = _CS_MESSAGES.get(action_type, {})
    return action_msgs.get(diagnosis, f"[fraud_account_lock] {diagnosis}")


def get_customer_message(diagnosis: str) -> str:
    """Get customer-facing message for a fraud/account lock diagnosis.

    Args:
        diagnosis: The internal diagnosis code.

    Returns:
        Customer-friendly message in Vietnamese.
    """
    return _CUSTOMER_MESSAGES.get(diagnosis, f"Chúng tôi đang xử lý yêu cầu của bạn. Vui lòng chờ phản hồi.")
