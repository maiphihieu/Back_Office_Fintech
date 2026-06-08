"""Message templates for merchant settlement delay use case.

Maps internal diagnosis codes to human-readable messages for:
- CS/Ops staff (cs_message): Vietnamese, professional internal language
- Merchant-facing (merchant_message): Vietnamese, professional, no sensitive details

SAFETY:
  - Do not expose internal error codes to merchant
  - Do not promise payout has been executed if only draft exists
  - Do not mention approval as if already completed
  - Do not blame merchant harshly
"""

from __future__ import annotations

from fintech_agent.schemas.enums import ActionType

# ═══════════════════════════════════════════════════════════════
#  CS/Ops messages (internal — shown in Back Office dashboard)
# ═══════════════════════════════════════════════════════════════

_CS_MESSAGES: dict[str, dict[str, str]] = {
    ActionType.CREATE_MANUAL_PAYOUT_DRAFT: {
        "batch_failed_create_manual_payout": (
            "Batch settlement failed/không tạo được payout tự động. "
            "Merchant có net_settlement_amount > 0, bank account verified. "
            "Tạo draft manual payout. Tiền đang ở: trước giai đoạn payout. "
            "Cần phê duyệt trước khi thực hiện chuyển khoản."
        ),
        "payout_failed_retriable_retry_payout": (
            "Payout đã được tạo nhưng chuyển khoản thất bại do lỗi tạm thời "
            "(bank_timeout / system_error). Tạo draft retry payout. "
            "Tiền đang ở: settlement pool, chưa đến bank merchant. "
            "Cần phê duyệt trước khi retry."
        ),
    },
    ActionType.SEND_UNC_EMAIL_DRAFT: {
        "payout_success_unc_not_sent": (
            "Payout đã thành công nhưng UNC/biên lai chưa gửi cho merchant. "
            "Tạo draft email gửi UNC. Tiền đã ở: bank account merchant. "
            "Không tạo payout mới."
        ),
    },
    ActionType.REQUEST_BANK_ACCOUNT_CORRECTION: {
        "bank_account_verification_pending": (
            "Tài khoản ngân hàng của merchant đang chờ xác minh. "
            "Không thể tạo payout cho đến khi bank account verified."
        ),
        "bank_account_verification_rejected": (
            "Tài khoản ngân hàng bị từ chối xác minh. "
            "Yêu cầu merchant cập nhật thông tin ngân hàng."
        ),
        "bank_account_inactive": (
            "Tài khoản ngân hàng của merchant đã bị vô hiệu hóa. "
            "Yêu cầu merchant cung cấp tài khoản ngân hàng mới."
        ),
        "bank_account_name_mismatch": (
            "Tên chủ tài khoản ngân hàng không khớp tên merchant. "
            "Yêu cầu merchant xác nhận lại thông tin ngân hàng."
        ),
        "bank_account_missing": (
            "Merchant chưa đăng ký tài khoản ngân hàng nhận tiền. "
            "Yêu cầu merchant bổ sung thông tin ngân hàng."
        ),
    },
    ActionType.DRAFT_CUSTOMER_RESPONSE: {
        "settlement_not_due_yet": (
            "Settlement chưa đến hạn thanh toán (due_date chưa tới). "
            "Thông báo cho merchant thời điểm dự kiến giải ngân."
        ),
        "net_settlement_zero_or_negative": (
            "Net settlement amount = 0 hoặc âm do phí/hoàn tiền/chargeback "
            "đã offset hết gross amount. Gửi sao kê settlement cho merchant. "
            "Tiền đang ở: không có khoản giải ngân."
        ),
        "payout_in_progress_monitor": (
            "Payout đã được tạo và đang xử lý tại ngân hàng. "
            "KHÔNG tạo payout mới — rủi ro trùng payout cao. "
            "Tiền đang ở: đang trên đường chuyển đến bank merchant."
        ),
        "payout_success_unc_already_sent": (
            "Payout đã thành công và UNC đã gửi cho merchant. "
            "KHÔNG tạo payout mới. Gửi lại reference UNC. "
            "Tiền đã ở: bank account merchant."
        ),
    },
    ActionType.REQUEST_IDENTITY_CORRECTION: {
        "merchant_not_found": (
            "Không tìm thấy merchant khớp với thông tin khách cung cấp trong hệ thống. "
            "Vì chưa định danh được merchant, agent chưa thể kiểm tra settlement ledger, "
            "payout, tài khoản nhận tiền hoặc UNC. "
            "Tiền đang ở: chưa thể xác định vì chưa tìm thấy hồ sơ merchant."
        ),
    },
    ActionType.MANUAL_SETTLEMENT_REVIEW: {
        "merchant_on_hold_escalate_ops": (
            "Merchant đang ở trạng thái on_hold. "
            "Không giải ngân. Escalate Ops/Risk để kiểm tra. "
            "Tiền đang ở: settlement pool, giữ lại do on_hold."
        ),
        "settlement_ledger_not_found": (
            "Không tìm thấy settlement ledger cho merchant. "
            "Không có dữ liệu thanh toán để xử lý. Cần kiểm tra thủ công."
        ),
        "payout_amount_less_than_ledger_difference_payout": (
            "Payout đã thành công nhưng số tiền payout < ledger net amount. "
            "Có chênh lệch cần bù payout bổ sung hoặc review. "
            "Tiền đang ở: một phần ở bank merchant, phần chênh lệch ở settlement pool."
        ),
        "payout_failed_non_retriable": (
            "Payout thất bại do lỗi không thể retry (bank_rejected, v.v.). "
            "Cần kiểm tra thủ công và liên hệ ngân hàng. "
            "Tiền đang ở: settlement pool."
        ),
        "unknown_or_conflicting_evidence": (
            "Evidence không rõ ràng hoặc mâu thuẫn. "
            "Chuyển manual review để kiểm tra thủ công."
        ),
    },
}


# ═══════════════════════════════════════════════════════════════
#  Merchant-facing messages (external)
# ═══════════════════════════════════════════════════════════════

_MERCHANT_MESSAGES: dict[str, str] = {
    # Manual payout
    "batch_failed_create_manual_payout": (
        "Chúng tôi đã ghi nhận yêu cầu về khoản thanh toán D+1. "
        "Đội ngũ Settlement đang xử lý và chuẩn bị yêu cầu giải ngân thủ công "
        "theo quy trình phê duyệt nội bộ. "
        "Chúng tôi sẽ cập nhật kết quả sau khi hoàn tất xác minh."
    ),
    "payout_failed_retriable_retry_payout": (
        "Chúng tôi nhận thấy lệnh chuyển khoản trước đó gặp lỗi tạm thời. "
        "Đội ngũ đang chuẩn bị yêu cầu chuyển khoản lại theo quy trình nội bộ. "
        "Chúng tôi sẽ cập nhật khi có kết quả."
    ),
    # UNC
    "payout_success_unc_not_sent": (
        "Hệ thống ghi nhận khoản thanh toán đã được chuyển thành công. "
        "Chúng tôi sẽ gửi biên lai chuyển khoản (UNC) để quý đối tác "
        "đối chiếu với ngân hàng nhận."
    ),
    # Bank account
    "bank_account_verification_pending": (
        "Thông tin tài khoản ngân hàng nhận tiền cần được xác minh. "
        "Vui lòng kiểm tra lại số tài khoản, tên chủ tài khoản và chi nhánh ngân hàng."
    ),
    "bank_account_verification_rejected": (
        "Thông tin tài khoản ngân hàng nhận tiền cần được cập nhật. "
        "Vui lòng xác nhận lại số tài khoản, tên chủ tài khoản và ngân hàng nhận."
    ),
    "bank_account_inactive": (
        "Tài khoản ngân hàng đã đăng ký không còn hoạt động. "
        "Vui lòng cung cấp tài khoản ngân hàng mới để nhận thanh toán."
    ),
    "bank_account_name_mismatch": (
        "Tên chủ tài khoản ngân hàng chưa khớp với thông tin đăng ký. "
        "Vui lòng xác nhận lại thông tin tài khoản ngân hàng nhận tiền."
    ),
    "bank_account_missing": (
        "Chưa có thông tin tài khoản ngân hàng nhận tiền trong hệ thống. "
        "Vui lòng bổ sung thông tin tài khoản ngân hàng để nhận thanh toán."
    ),
    # Not due / zero net
    "settlement_not_due_yet": (
        "Khoản thanh toán theo chu kỳ settlement chưa đến hạn giải ngân. "
        "Vui lòng chờ đến thời điểm giải ngân theo lịch trình."
    ),
    "net_settlement_zero_or_negative": (
        "Sau khi trừ phí dịch vụ, hoàn tiền và chargeback, "
        "khoản thanh toán ròng trong kỳ này bằng 0 hoặc âm. "
        "Chúng tôi sẽ gửi sao kê chi tiết để quý đối tác đối chiếu."
    ),
    # In progress / success
    "payout_in_progress_monitor": (
        "Lệnh chuyển khoản đã được tạo và đang xử lý tại ngân hàng. "
        "Vui lòng kiểm tra tài khoản ngân hàng nhận tiền. "
        "Chúng tôi sẽ cập nhật kết quả sớm nhất."
    ),
    "payout_success_unc_already_sent": (
        "Khoản thanh toán đã được chuyển thành công và biên lai (UNC) "
        "đã được gửi trước đó. Vui lòng kiểm tra email hoặc liên hệ "
        "ngân hàng nhận để xác nhận."
    ),
    # Manual review
    "merchant_not_found": (
        "Chúng tôi cần xác nhận lại thông tin đối tác. "
        "Vui lòng cung cấp mã merchant, số điện thoại, email hoặc mã số thuế."
    ),
    "merchant_on_hold_escalate_ops": (
        "Tài khoản đối tác đang trong quá trình xem xét nội bộ. "
        "Bộ phận phụ trách sẽ liên hệ cập nhật tình trạng."
    ),
    "settlement_ledger_not_found": (
        "Chúng tôi đang kiểm tra dữ liệu thanh toán. "
        "Bộ phận Settlement sẽ cập nhật kết quả sớm nhất."
    ),
    "payout_amount_less_than_ledger_difference_payout": (
        "Chúng tôi nhận thấy có chênh lệch giữa số tiền đã chuyển "
        "và tổng thanh toán ròng trong kỳ. Đội ngũ đang kiểm tra "
        "và sẽ cập nhật kết quả."
    ),
    "payout_failed_non_retriable": (
        "Lệnh chuyển khoản gặp sự cố từ phía ngân hàng. "
        "Đội ngũ đang phối hợp kiểm tra và sẽ cập nhật kết quả."
    ),
    "unknown_or_conflicting_evidence": (
        "Chúng tôi đang kiểm tra yêu cầu của quý đối tác. "
        "Bộ phận phụ trách sẽ liên hệ cập nhật kết quả."
    ),
}


def get_cs_message(action_type: ActionType, diagnosis: str) -> str:
    """Get CS/Ops-facing message for a merchant settlement diagnosis.

    Args:
        action_type: The recommended action type.
        diagnosis: The internal diagnosis code.

    Returns:
        Human-readable message for CS staff. Falls back to diagnosis code.
    """
    action_msgs = _CS_MESSAGES.get(action_type, {})
    return action_msgs.get(diagnosis, f"[merchant_settlement] {diagnosis}")


def get_merchant_message(diagnosis: str) -> str:
    """Get merchant-facing message for a settlement diagnosis.

    Args:
        diagnosis: The internal diagnosis code.

    Returns:
        Merchant-friendly message in Vietnamese.
    """
    return _MERCHANT_MESSAGES.get(
        diagnosis,
        "Chúng tôi đã ghi nhận yêu cầu và đang xử lý. "
        "Bộ phận phụ trách sẽ cập nhật kết quả sớm nhất.",
    )
