"""Deterministic resolution ticket builder.

Builds a ResolutionTicket from state + rule engine output + evidence.
NO LLM involvement in action/tool decisions.

The LLM's GeneratedResponse is used only for human-readable text fields
(issue_summary, problem_explanation, customer_reply_draft).
All action/tool/status fields come from the rule engine mapping.
"""

from __future__ import annotations

import logging
from typing import Any

from fintech_agent.schemas.claim_verification import ClaimVerificationSummary
from fintech_agent.schemas.resolution_ticket import AmountVerification, ResolutionTicket, TicketAction
from fintech_agent.schemas.response_generation import GeneratedResponse

logger = logging.getLogger(__name__)

# ─── ActionType → TicketAction Mapping ──────────────────────────

# Static lookup: each ActionType maps to a known MCP tool, name, and execution mode.
# This is the ONLY source of truth for action → tool mapping.

_ACTION_MAP: dict[str, dict[str, Any]] = {
    "create_refund_request_draft": {
        "action_name": "Tạo yêu cầu hoàn tiền (draft)",
        "description": (
            "Tạo draft yêu cầu hoàn tiền cho giao dịch. "
            "Số tiền hoàn lấy từ wallet_ledger.debit_amount, không từ complaint."
        ),
        "mcp_tool": "create_refund_draft",
        "execution_mode": "draft_only",
        "preconditions": [
            "transaction.status = completed hoặc failed",
            "wallet_ledger.status = debited",
            "refund_status = not_requested",
        ],
        "evidence_dependencies": ["transaction", "wallet_ledger", "refund_status"],
        "expected_result": (
            "Một draft refund request sẽ được tạo để nhân viên review. "
            "Không tự động trừ/cộng tiền, không sửa ledger."
        ),
        "safety_notes": [
            "Không tự động thực hiện refund",
            "Số tiền phải lấy từ wallet_ledger, không từ complaint",
            "Cần phê duyệt trước khi thực hiện",
        ],
    },
    "create_force_success_draft": {
        "action_name": "Tạo lệnh force-success giao dịch (draft)",
        "description": (
            "Tạo draft force-success để đánh dấu giao dịch top-up đang pending "
            "thành công, sau khi bank reconciliation xác nhận bank đã nhận tiền."
        ),
        "mcp_tool": "create_force_success_draft",
        "execution_mode": "draft_only",
        "preconditions": [
            "transaction.status = pending",
            "bank_reconciliation.bank_status = success",
            "money_received_in_master_wallet = true",
        ],
        "evidence_dependencies": ["transaction", "wallet_ledger", "reconciliation_status"],
        "expected_result": (
            "Một draft Force Success sẽ được tạo để nhân viên review. "
            "Không tự động cập nhật wallet balance hoặc ledger."
        ),
        "safety_notes": [
            "Không tự động update wallet balance",
            "Không tự động edit ledger",
            "Cần phê duyệt cấp cao trước khi thực hiện",
        ],
    },
    "create_reconciliation_ticket_draft": {
        "action_name": "Tạo ticket đối soát (draft)",
        "description": (
            "Tạo ticket đối soát cho trường hợp mâu thuẫn dữ liệu "
            "giữa ví, bank và/hoặc provider."
        ),
        "mcp_tool": "create_reconciliation_draft",
        "execution_mode": "draft_only",
        "preconditions": [
            "Có mâu thuẫn giữa dữ liệu ví, bank, hoặc provider",
        ],
        "evidence_dependencies": ["transaction", "wallet_ledger", "reconciliation_status"],
        "expected_result": (
            "Một ticket đối soát sẽ được tạo. "
            "Team reconciliation sẽ kiểm tra và xử lý."
        ),
        "safety_notes": [
            "Không tự động sửa dữ liệu đối soát",
            "Chỉ tạo ticket để team reconciliation kiểm tra",
        ],
    },
    "create_unlock_account_draft": {
        "action_name": "Tạo yêu cầu mở khóa tài khoản",
        "description": (
            "Dữ liệu rủi ro thấp và có khả năng khóa nhầm. "
            "Chỉ tạo bản nháp, chưa tự động mở khóa."
        ),
        "mcp_tool": "create_unlock_account_draft",
        "execution_mode": "draft_only",
        "preconditions": [
            "account_status = locked",
            "fraud_case.recommended_decision ≠ lock_confirmed",
            "risk_level = low hoặc medium",
        ],
        "evidence_dependencies": ["account_status", "fraud_case"],
        "expected_result": (
            "Một draft unlock account sẽ được tạo. "
            "Cần phê duyệt từ Risk/Ops trước khi unlock."
        ),
        "safety_notes": [
            "Không tự động unlock account",
            "Không bỏ qua phê duyệt",
            "Không tiết lộ rule fraud cho khách",
        ],
    },
    "draft_customer_response": {
        "action_name": "Soạn câu trả lời cho khách hàng",
        "description": (
            "Soạn nháp câu trả lời gửi cho khách hàng dựa trên kết quả phân tích."
        ),
        "mcp_tool": "create_customer_response_draft",
        "execution_mode": "draft_only",
        "preconditions": [],
        "evidence_dependencies": ["transaction"],
        "expected_result": (
            "Draft câu trả lời sẽ được tạo. "
            "Nhân viên review trước khi gửi."
        ),
        "safety_notes": [
            "Không tiết lộ rule nội bộ hoặc threshold",
            "Không nói kết quả trước khi xác nhận",
        ],
    },
    "create_request_documents_response_draft": {
        "action_name": "Yêu cầu bổ sung chứng từ từ khách hàng",
        "description": (
            "Soạn thư yêu cầu khách hàng bổ sung thông tin hoặc chứng từ cần thiết."
        ),
        "mcp_tool": "create_customer_response_draft",
        "execution_mode": "draft_only",
        "preconditions": [
            "Thiếu thông tin cần thiết từ khách hàng",
        ],
        "evidence_dependencies": [],
        "expected_result": (
            "Draft yêu cầu bổ sung sẽ được tạo. "
            "Nhân viên review nội dung trước khi gửi."
        ),
        "safety_notes": [
            "Không yêu cầu OTP, PIN, mật khẩu",
            "Chỉ yêu cầu thông tin liên quan đến case",
        ],
    },
    "manual_review": {
        "action_name": "Chuyển cho nhân viên review thủ công",
        "description": (
            "Case cần được nhân viên kiểm tra thủ công do evidence không đủ "
            "hoặc tình huống phức tạp không thể tự động xử lý."
        ),
        "mcp_tool": None,
        "execution_mode": "manual",
        "preconditions": [],
        "evidence_dependencies": [],
        "expected_result": (
            "Case sẽ được chuyển sang trạng thái manual review. "
            "Nhân viên cần kiểm tra và quyết định bước tiếp theo."
        ),
        "safety_notes": [
            "Không tự động thực hiện action nào",
            "Nhân viên cần review toàn bộ evidence",
        ],
    },
    "request_identity_correction": {
        "action_name": "Yêu cầu bổ sung/correct thông tin định danh",
        "description": (
            "Không tìm thấy tài khoản khớp với thông tin khách cung cấp. "
            "Cần yêu cầu khách kiểm tra lại số điện thoại/email/wallet_id "
            "hoặc cung cấp mã giao dịch gần nhất."
        ),
        "mcp_tool": None,
        "execution_mode": "manual",
        "preconditions": [
            "Thông tin định danh khách cung cấp không khớp tài khoản nào",
        ],
        "evidence_dependencies": [],
        "expected_result": (
            "Nhân viên liên hệ khách yêu cầu xác nhận lại thông tin. "
            "Không có thao tác nào trên tài khoản."
        ),
        "safety_notes": [
            "Không tự động mở khóa hoặc thao tác tài khoản nào",
            "Không fetch fraud evidence cho user khác",
            "Chỉ yêu cầu khách bổ sung thông tin định danh",
        ],
    },
    # ── Merchant settlement actions ──────────────────────────
    "create_manual_payout_draft": {
        "action_name": "Tạo yêu cầu giải ngân thủ công (draft)",
        "description": (
            "Settlement batch thất bại hoặc payout lỗi, cần tạo manual payout. "
            "Tiền đang ở settlement pool, chưa đến bank merchant. "
            "Số tiền lấy từ settlement_ledger.net_settlement_amount."
        ),
        "mcp_tool": None,
        "execution_mode": "draft_only",
        "preconditions": [
            "merchant.status = active",
            "bank_account.verification_status = verified",
            "settlement_ledger.net_settlement_amount > 0",
            "Không có payout đang processing/success",
        ],
        "evidence_dependencies": [
            "merchant_profile", "merchant_bank_account",
            "merchant_settlement_ledger", "merchant_payout",
            "settlement_batch",
        ],
        "expected_result": (
            "Draft manual payout sẽ được tạo. "
            "Cần phê duyệt cấp cao trước khi thực hiện chuyển khoản."
        ),
        "safety_notes": [
            "Không tự động thực hiện payout",
            "Số tiền lấy từ settlement_ledger, không từ merchant",
            "Cần phê duyệt trước khi thực hiện",
            "Kiểm tra bank account verified trước khi duyệt",
            "Kiểm tra duplicate payout risk",
        ],
    },
    "send_unc_email_draft": {
        "action_name": "Gửi UNC/biên lai chuyển khoản cho merchant (draft)",
        "description": (
            "Payout đã thành công nhưng UNC chưa gửi cho merchant. "
            "Tiền đã ở bank account merchant. Không tạo payout mới."
        ),
        "mcp_tool": None,
        "execution_mode": "draft_only",
        "preconditions": [
            "payout.status = success",
            "bank_transfer_receipt.sent_to_merchant = false",
        ],
        "evidence_dependencies": [
            "merchant_profile", "merchant_payout", "bank_transfer_receipt",
        ],
        "expected_result": (
            "Draft email UNC sẽ được tạo. "
            "Nhân viên review nội dung trước khi gửi."
        ),
        "safety_notes": [
            "Không tạo payout mới",
            "Không gửi email thật — chỉ tạo draft",
        ],
    },
    "request_bank_account_correction": {
        "action_name": "Yêu cầu merchant cập nhật tài khoản ngân hàng",
        "description": (
            "Tài khoản ngân hàng merchant không hợp lệ hoặc chưa xác minh. "
            "Không thể tạo payout cho đến khi bank account verified."
        ),
        "mcp_tool": None,
        "execution_mode": "draft_only",
        "preconditions": [
            "bank_account invalid/inactive/pending/name_mismatch",
        ],
        "evidence_dependencies": [
            "merchant_profile", "merchant_bank_account",
        ],
        "expected_result": (
            "Draft yêu cầu cập nhật bank account sẽ được tạo. "
            "Merchant cần cung cấp thông tin ngân hàng mới."
        ),
        "safety_notes": [
            "Không tự động update bank account",
            "Không tạo payout khi bank account chưa verified",
        ],
    },
    "manual_settlement_review": {
        "action_name": "Chuyển Settlement team review thủ công",
        "description": (
            "Case settlement phức tạp hoặc evidence không đủ. "
            "Cần Settlement/Ops team kiểm tra thủ công."
        ),
        "mcp_tool": None,
        "execution_mode": "manual",
        "preconditions": [],
        "evidence_dependencies": [
            "merchant_profile", "merchant_settlement_ledger",
            "merchant_payout", "settlement_batch",
        ],
        "expected_result": (
            "Case sẽ được chuyển sang Settlement manual review. "
            "Nhân viên cần kiểm tra toàn bộ evidence và quyết định."
        ),
        "safety_notes": [
            "Không tự động thực hiện payout",
            "Không tự động update bank account",
            "Nhân viên cần review toàn bộ evidence",
        ],
    },
    "request_identity_correction": {
        "action_name": "Yêu cầu bổ sung thông tin định danh Merchant",
        "description": (
            "Không tìm thấy merchant khớp với thông tin khách cung cấp trong hệ thống. "
            "Vì chưa định danh được merchant, agent chưa thể kiểm tra settlement ledger, "
            "payout, tài khoản nhận tiền hoặc UNC."
        ),
        "mcp_tool": None,
        "execution_mode": "information_request",
        "preconditions": [],
        "evidence_dependencies": [],
        "expected_result": (
            "Nhân viên yêu cầu merchant cung cấp lại thông tin định danh "
            "(merchant_id, phone/email, MST, payout_id hoặc batch_id)."
        ),
        "safety_notes": [
            "Không tạo payout",
            "Không gửi UNC",
            "Không cập nhật tài khoản ngân hàng",
            "Không fallback sang merchant khác",
            "Cần định danh merchant trước khi kiểm tra settlement",
        ],
    },
    "wait_sla": {
        "action_name": "Chờ SLA / thời gian xử lý",
        "description": (
            "Chờ SLA xử lý từ bank/provider. "
            "Theo dõi và cập nhật cho khách khi có kết quả."
        ),
        "mcp_tool": None,
        "execution_mode": "manual",
        "preconditions": [],
        "evidence_dependencies": ["transaction"],
        "expected_result": (
            "Hệ thống sẽ theo dõi SLA. "
            "Nhân viên cập nhật cho khách sau khi có kết quả."
        ),
        "safety_notes": [],
    },
    "no_action": {
        "action_name": "Không cần hành động",
        "description": "Giao dịch đã được xử lý hoặc không có vấn đề.",
        "mcp_tool": None,
        "execution_mode": "read_only",
        "preconditions": [],
        "evidence_dependencies": [],
        "expected_result": "Không cần thêm action.",
        "safety_notes": [],
    },
}


# ─── Staff Instruction Templates ────────────────────────────────

_STAFF_INSTRUCTIONS: dict[str, str] = {
    "create_refund_request_draft": (
        "Xem xét evidence và draft refund. Kiểm tra số tiền, giao dịch gốc, "
        "và trạng thái ví trước khi phê duyệt."
    ),
    "create_force_success_draft": (
        "Kiểm tra bank reconciliation, wallet ledger và trạng thái giao dịch. "
        "Force-success chỉ khi bank đã confirm nhận tiền. Cần phê duyệt cấp cao."
    ),
    "create_reconciliation_ticket_draft": (
        "Kiểm tra dữ liệu đối soát giữa ví, bank và provider. "
        "Tạo ticket đối soát và theo dõi kết quả."
    ),
    "create_unlock_account_draft": (
        "Kiểm tra đầy đủ fraud evidence trước khi phê duyệt:\n"
        "• Trạng thái tài khoản\n"
        "• Trạng thái rút tiền\n"
        "• Mức rủi ro / Điểm rủi ro\n"
        "• Kết quả rà soát fraud\n"
        "• Tình trạng KYC\n"
        "• Tín hiệu thiết bị/đăng nhập\n"
        "• Giao dịch đáng ngờ\n"
        "Cần Risk/Ops phê duyệt trước khi xử lý."
    ),
    "draft_customer_response": (
        "Xem lại nội dung trả lời nháp cho khách. Đảm bảo không tiết lộ "
        "thông tin nội bộ, rule, threshold."
    ),
    "create_request_documents_response_draft": (
        "Yêu cầu khách bổ sung thông tin: mã giao dịch, biên lai, hoặc ảnh chụp. "
        "Không yêu cầu OTP, PIN, mật khẩu."
    ),
    "manual_review": (
        "Case cần review thủ công. Kiểm tra reconciliation file, verify provider status, "
        "hoặc liên hệ bank/provider nếu cần. Escalate lên Risk/Ops nếu phức tạp."
    ),
    "request_identity_correction": (
        "Yêu cầu khách xác nhận lại thông tin định danh: "
        "số điện thoại, email, wallet_id hoặc mã giao dịch. "
        "Không thao tác tài khoản cho đến khi định danh được."
    ),
    # ── Merchant settlement staff instructions ──
    "create_manual_payout_draft": (
        "Kiểm tra trước khi phê duyệt manual payout:\n"
        "• Merchant status = active\n"
        "• Bank account verified\n"
        "• Số tiền = settlement_ledger.net_settlement_amount\n"
        "• Không có payout đang processing/success (duplicate risk)\n"
        "• Tiền đang ở: settlement pool, chưa đến bank merchant\n"
        "Cần phê duyệt cấp cao trước khi chuyển khoản."
    ),
    "send_unc_email_draft": (
        "Payout đã thành công. KHÔNG tạo payout mới.\n"
        "• Tiền đã ở: bank account merchant\n"
        "• Review nội dung UNC/biên lai trước khi gửi cho merchant\n"
        "• Kiểm tra payout_id, unc_number, receipt_url chính xác"
    ),
    "request_bank_account_correction": (
        "Bank account merchant không hợp lệ hoặc chưa xác minh.\n"
        "• KHÔNG tạo payout khi bank account chưa verified\n"
        "• Yêu cầu merchant cập nhật: số tài khoản, tên chủ TK, chi nhánh\n"
        "• Sau khi merchant update, kiểm tra lại verification status"
    ),
    "manual_settlement_review": (
        "Case settlement cần review thủ công.\n"
        "• Kiểm tra merchant profile, settlement ledger, payout status, batch status\n"
        "• Xác định: Tiền đang ở đâu? (settlement pool / bank / merchant)\n"
        "• Liên hệ bank/settlement team nếu cần\n"
        "• Escalate Ops/Risk nếu phức tạp"
    ),
    "request_identity_correction": (
        "Chưa tìm thấy merchant trong hệ thống.\n"
        "• Yêu cầu merchant cung cấp lại merchant_id, số điện thoại/email đăng ký merchant, "
        "mã số thuế, payout_id hoặc batch_id nếu có\n"
        "• KHÔNG tạo payout, UNC, hoặc cập nhật bank account\n"
        "• KHÔNG fallback sang merchant khác\n"
        "• Cần định danh merchant trước khi kiểm tra settlement"
    ),
    "wait_sla": (
        "Chờ SLA xử lý từ bank/provider. Theo dõi và cập nhật cho khách sau khi có kết quả."
    ),
    "no_action": (
        "Không cần hành động thêm. Giao dịch đã được xử lý hoặc không có vấn đề."
    ),
}

_DEFAULT_STAFF_INSTRUCTION = (
    "Nhân viên xem lại toàn bộ evidence, action đề xuất, và trạng thái phê duyệt "
    "trước khi quyết định bước tiếp theo."
)

# Fraud-specific staff instructions (override generic ones)
_FRAUD_STAFF_INSTRUCTIONS: dict[str, str] = {
    "manual_review": (
        "Kiểm tra fraud case, risk signals, lịch sử giao dịch gần đây, "
        "thiết bị đăng nhập, trạng thái KYC. "
        "Nếu thiếu chứng từ, yêu cầu khách bổ sung giấy tờ xác minh. "
        "Escalate Risk/Ops nếu bằng chứng chưa đủ để kết luận."
    ),
    "request_identity_correction": (
        "Yêu cầu khách xác nhận lại số điện thoại đăng ký ví, email, "
        "wallet_id hoặc cung cấp mã giao dịch gần nhất. "
        "Sau khi định danh được tài khoản, nhân viên mới kiểm tra "
        "account_status, withdrawal_status, fraud_case, risk signals và KYC."
    ),
    "create_request_documents_response_draft": (
        "Yêu cầu khách bổ sung giấy tờ xác minh danh tính: CMND/CCCD, ảnh chụp selfie. "
        "Không yêu cầu OTP, PIN, mật khẩu. "
        "Tài khoản vẫn giữ trạng thái khóa trong thời gian xác minh."
    ),
}

# ─── Evidence Field Definitions ─────────────────────────────────

# Known evidence fields and their human-readable names
_EVIDENCE_FIELDS: dict[str, str] = {
    "transaction": "Dữ liệu giao dịch",
    "wallet_ledger": "Sổ cái ví",
    "provider_status": "Trạng thái nhà cung cấp",
    "train_provider": "Trạng thái nhà cung cấp vé tàu",
    "utility_provider": "Trạng thái nhà cung cấp tiện ích",
    "refund_status": "Trạng thái hoàn tiền",
    "reconciliation_status": "Dữ liệu đối soát",
    "account_status": "Trạng thái tài khoản",
    "fraud_case": "Dữ liệu fraud/risk",
    # Merchant settlement evidence
    "merchant_profile": "Thông tin merchant",
    "merchant_bank_account": "Tài khoản ngân hàng merchant",
    "merchant_settlement_ledger": "Sổ settlement ledger",
    "merchant_payout": "Trạng thái payout merchant",
    "settlement_batch": "Trạng thái settlement batch",
    "bank_transfer_receipt": "Biên lai chuyển khoản / UNC",
}


# ─── Builder ────────────────────────────────────────────────────


def _extract_action_type(state: dict[str, Any]) -> str | None:
    """Extract the action_type string from state."""
    action = state.get("recommended_action")
    if action is not None:
        if hasattr(action, "action_type"):
            at = action.action_type
            return at.value if hasattr(at, "value") else str(at)
        if isinstance(action, str):
            return action
    # Fallback to rule_decision
    rd = state.get("rule_decision")
    if isinstance(rd, dict):
        return rd.get("action")
    return None


def _extract_approval_required(state: dict[str, Any]) -> bool:
    """Extract approval_required from state."""
    action = state.get("recommended_action")
    if action is not None and hasattr(action, "approval_required"):
        return action.approval_required
    return state.get("approval_required", False)


def _extract_risk_level(state: dict[str, Any]) -> str:
    """Extract risk_level string from state."""
    action = state.get("recommended_action")
    if action is not None and hasattr(action, "risk_level"):
        rl = action.risk_level
        return rl.value if hasattr(rl, "value") else str(rl)
    rl = state.get("risk_level")
    if rl is not None:
        return rl.value if hasattr(rl, "value") else str(rl)
    return "unknown"


def _extract_diagnosis(state: dict[str, Any]) -> str:
    """Extract diagnosis string from state."""
    action = state.get("recommended_action")
    if action is not None and hasattr(action, "diagnosis"):
        return action.diagnosis or ""
    rd = state.get("rule_decision")
    if isinstance(rd, dict):
        return rd.get("diagnosis", "")
    return ""


def _compute_evidence_checked_and_missing(
    state: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Determine which evidence was checked and which is missing.

    Workflow-aware: different workflows expect different evidence.
    - fraud_account_lock: expects account_status, fraud_case
    - transaction workflows: expects transaction, wallet_ledger
    """
    eb = state.get("evidence_bundle") or state.get("evidence")
    checked: list[str] = []
    missing: list[str] = []

    if eb is None:
        missing = list(_EVIDENCE_FIELDS.values())
        return checked, missing

    if hasattr(eb, "model_dump"):
        eb_dict = eb.model_dump(mode="json", exclude_none=True)
    elif isinstance(eb, dict):
        eb_dict = eb
    else:
        missing = list(_EVIDENCE_FIELDS.values())
        return checked, missing

    for field_key, field_label in _EVIDENCE_FIELDS.items():
        val = eb_dict.get(field_key)
        if val is not None:
            checked.append(field_label)

    # Determine expected evidence based on workflow
    selected_workflow = state.get("selected_workflow")

    if selected_workflow == "fraud_account_lock":
        # Fraud workflow expects account_status + fraud_case
        expected_keys = ("account_status", "fraud_case")
    elif selected_workflow == "merchant_settlement_delay":
        # Merchant settlement expects settlement-specific evidence
        expected_keys = (
            "merchant_profile", "merchant_bank_account",
            "merchant_settlement_ledger", "merchant_payout",
            "settlement_batch", "bank_transfer_receipt",
        )
    else:
        # Transaction workflows expect transaction + wallet_ledger
        expected_keys = ("transaction", "wallet_ledger")

    for key in expected_keys:
        if eb_dict.get(key) is None and _EVIDENCE_FIELDS.get(key) not in checked:
            label = _EVIDENCE_FIELDS.get(key, key)
            if label not in missing:
                missing.append(label)

    # ── Fraud sub-evidence: report granular signals when available ──
    if selected_workflow == "fraud_account_lock":
        fraud_data = eb_dict.get("fraud_case")
        if isinstance(fraud_data, dict):
            if fraud_data.get("risk_score") is not None:
                checked.append("Mức rủi ro / Điểm rủi ro")
            if fraud_data.get("fraud_status"):
                checked.append("Trạng thái fraud")
            if fraud_data.get("recommended_decision"):
                checked.append("Kết quả rà soát fraud")
            if fraud_data.get("device_events"):
                checked.append("Tín hiệu thiết bị/đăng nhập")
            if fraud_data.get("recent_transactions"):
                checked.append("Giao dịch đáng ngờ gần đây")
        acct_data = eb_dict.get("account_status")
        if isinstance(acct_data, dict):
            if acct_data.get("withdrawal_enabled") is not None:
                checked.append("Trạng thái rút tiền")

    # ── Wallet topup sub-evidence: report bank reconciliation details ──
    if selected_workflow == "wallet_topup":
        txn_data = eb_dict.get("transaction")
        if isinstance(txn_data, dict):
            if txn_data.get("status"):
                checked.append("Trạng thái giao dịch")
            if txn_data.get("amount") is not None:
                checked.append("Số tiền giao dịch")
        recon_data = eb_dict.get("reconciliation_status")
        if isinstance(recon_data, dict):
            if recon_data.get("bank_status"):
                checked.append("Trạng thái bank")
            if recon_data.get("money_received_in_master_wallet") is not None:
                checked.append("Tiền vào master wallet")
            if recon_data.get("bank_amount") is not None:
                checked.append("Số tiền bank")
        wl_data = eb_dict.get("wallet_ledger")
        if isinstance(wl_data, dict):
            if wl_data.get("status"):
                checked.append("Trạng thái wallet ledger")
        else:
            if "Wallet Ledger" not in missing:
                missing.append("Wallet Ledger")

    # ── Merchant settlement sub-evidence ──
    if selected_workflow == "merchant_settlement_delay":
        mp = eb_dict.get("merchant_profile")
        if isinstance(mp, dict):
            if mp.get("status"):
                checked.append("Trạng thái merchant")
            if mp.get("settlement_cycle"):
                checked.append("Chu kỳ thanh toán")
        mba = eb_dict.get("merchant_bank_account")
        if isinstance(mba, dict):
            if mba.get("verification_status"):
                checked.append("Trạng thái xác minh tài khoản ngân hàng")
            if mba.get("is_active") is not None:
                checked.append("Bank account active")
        msl = eb_dict.get("merchant_settlement_ledger")
        if isinstance(msl, dict):
            if msl.get("net_settlement_amount") is not None:
                checked.append("Số tiền thanh toán ròng")
            if msl.get("due_date"):
                checked.append("Ngày đáo hạn")
            if msl.get("settlement_date"):
                checked.append("Ngày settlement")
        mpay = eb_dict.get("merchant_payout")
        if isinstance(mpay, dict):
            if mpay.get("status"):
                checked.append("Trạng thái payout")
            if mpay.get("amount") is not None:
                checked.append("Số tiền payout")
        sb = eb_dict.get("settlement_batch")
        if isinstance(sb, dict):
            if sb.get("status"):
                checked.append("Trạng thái batch")
        btr = eb_dict.get("bank_transfer_receipt")
        if isinstance(btr, dict):
            if btr.get("unc_number"):
                checked.append("Số UNC")
            if btr.get("sent_to_merchant") is not None:
                checked.append("UNC đã gửi merchant")

    return checked, missing


def _determine_action_status(
    action_type: str,
    approval_required: bool,
    approval_status: str | None,
    case_status: str | None,
) -> str:
    """Determine the status of the action."""
    mapping = _ACTION_MAP.get(action_type, {})
    exec_mode = mapping.get("execution_mode", "manual")

    if exec_mode == "manual":
        return "manual_required"

    if approval_required:
        if approval_status == "approved":
            return "draft"
        if approval_status == "pending" or case_status == "waiting_approval":
            return "waiting_approval"
        return "waiting_approval"

    return "draft"


def _determine_resolution_status(
    action_type: str | None,
    missing_evidence: list[str],
    has_conflict: bool,
) -> str:
    """Determine overall resolution status."""
    if action_type is None:
        return "not_supported"

    if action_type == "manual_review":
        return "manual_review_required"

    if action_type == "manual_settlement_review":
        return "manual_review_required"

    if action_type == "request_identity_correction":
        return "missing_identity"

    mapping = _ACTION_MAP.get(action_type)
    if mapping is None:
        return "not_supported"

    if mapping.get("execution_mode") == "manual":
        return "manual_review_required"

    # If there are critical missing evidence, mark as manual review
    if len(missing_evidence) >= 2 or has_conflict:
        return "manual_review_required"

    return "actionable"


def _build_missing_evidence_instruction(missing: list[str]) -> str:
    """Generate additional instruction for missing evidence."""
    if not missing:
        return ""
    return f" Evidence còn thiếu: {', '.join(missing)}. Cần xác minh thêm trước khi xử lý."


def _build_mcp_input(state: dict[str, Any], action_type: str) -> dict[str, Any]:
    """Build MCP tool input from state evidence.

    Extracts relevant fields from state to construct the input
    that would be passed to the MCP tool. This is deterministic —
    no LLM involvement.
    """
    mcp_input: dict[str, Any] = {}

    # Extract common fields from state
    ei = state.get("extracted_info")
    if ei is not None:
        if hasattr(ei, "model_dump"):
            ei_dict = ei.model_dump(mode="json", exclude_none=True)
        elif isinstance(ei, dict):
            ei_dict = ei
        else:
            ei_dict = {}
    else:
        ei_dict = {}

    txn_id = ei_dict.get("transaction_id") or state.get("ticket_id", "")
    user_id = ei_dict.get("user_id") or state.get("user_id", "")

    # Extract amount from evidence bundle
    eb = state.get("evidence_bundle") or state.get("evidence")
    amount = None
    if eb is not None:
        if hasattr(eb, "model_dump"):
            eb_dict = eb.model_dump(mode="json", exclude_none=True)
        elif isinstance(eb, dict):
            eb_dict = eb
        else:
            eb_dict = {}
        wl = eb_dict.get("wallet_ledger")
        if isinstance(wl, dict):
            amount = wl.get("debit_amount") or wl.get("amount")

    # Extract diagnosis as reason
    diagnosis = _extract_diagnosis(state)

    if action_type == "create_refund_request_draft":
        mcp_input = {
            "transaction_id": txn_id,
            "user_id": user_id,
            "reason": diagnosis or "refund_requested",
        }
        if amount is not None:
            mcp_input["amount"] = amount
            mcp_input["amount_source"] = "wallet_ledger.debit_amount"

    elif action_type == "create_force_success_draft":
        mcp_input = {
            "transaction_id": txn_id,
            "reason": diagnosis or "bank_success_wallet_pending",
        }

    elif action_type == "create_reconciliation_ticket_draft":
        mcp_input = {
            "transaction_id": txn_id,
            "user_id": user_id,
            "mismatch_type": diagnosis or "data_mismatch",
        }

    elif action_type == "create_unlock_account_draft":
        mcp_input = {
            "user_id": user_id,
            "reason": diagnosis or "fraud_false_positive",
        }

    elif action_type in ("draft_customer_response", "create_request_documents_response_draft"):
        mcp_input = {
            "case_id": state.get("case_id", ""),
        }

    # ── Merchant settlement MCP inputs ──
    elif action_type == "create_manual_payout_draft":
        merchant_id = ei_dict.get("merchant_id", "")
        mcp_input = {
            "merchant_id": merchant_id,
            "reason": diagnosis or "manual_payout",
        }
        if eb is not None:
            ledger = eb_dict.get("merchant_settlement_ledger")
            if isinstance(ledger, dict) and ledger.get("net_settlement_amount"):
                mcp_input["amount"] = ledger["net_settlement_amount"]
                mcp_input["amount_source"] = "settlement_ledger.net_settlement_amount"

    elif action_type == "send_unc_email_draft":
        mcp_input = {
            "merchant_id": ei_dict.get("merchant_id", ""),
            "reason": diagnosis or "send_unc",
        }

    elif action_type == "request_bank_account_correction":
        mcp_input = {
            "merchant_id": ei_dict.get("merchant_id", ""),
            "reason": diagnosis or "bank_account_correction",
        }

    elif action_type == "manual_settlement_review":
        mcp_input = {
            "merchant_id": ei_dict.get("merchant_id", ""),
            "reason": diagnosis or "manual_settlement_review",
        }

    return mcp_input


# ─── Money-related action types that use amounts ────────────────────

_MONEY_ACTION_TYPES = frozenset({
    "create_refund_request_draft",
    "create_force_success_draft",
    "create_reconciliation_ticket_draft",
    "create_manual_payout_draft",
})


def _build_amount_verification(state: dict[str, Any]) -> AmountVerification:
    """Build amount verification metadata from state.

    Extracts customer-claimed amount from extracted_info and trusted amount
    from system evidence (wallet_ledger > transaction > reconciliation).

    SAFETY: Action amounts ALWAYS come from trusted system data.
    Customer-claimed amount is for reference only.
    """
    # Customer-claimed amount (from complaint extraction — reference only)
    ei = state.get("extracted_info")
    claimed: int | None = None
    if ei is not None:
        if hasattr(ei, "amount_claimed"):
            claimed = ei.amount_claimed
        elif isinstance(ei, dict):
            claimed = ei.get("amount_claimed")

    # Trusted amount from system evidence (priority order)
    eb = state.get("evidence_bundle") or state.get("evidence")
    trusted: int | None = None
    trusted_source: str | None = None

    if eb is not None:
        if hasattr(eb, "model_dump"):
            eb_dict = eb.model_dump(mode="json", exclude_none=True)
        elif isinstance(eb, dict):
            eb_dict = eb
        else:
            eb_dict = {}

        # Priority 0: settlement_ledger.net_settlement_amount (merchant settlement)
        msl = eb_dict.get("merchant_settlement_ledger")
        if isinstance(msl, dict) and msl.get("net_settlement_amount"):
            trusted = msl["net_settlement_amount"]
            trusted_source = "settlement_ledger.net_settlement_amount"
        # Priority 1: wallet_ledger.debit_amount
        elif isinstance(eb_dict.get("wallet_ledger"), dict) and eb_dict["wallet_ledger"].get("debit_amount"):
            trusted = eb_dict["wallet_ledger"]["debit_amount"]
            trusted_source = "wallet_ledger.debit_amount"
        # Priority 2: transaction.amount
        elif isinstance(eb_dict.get("transaction"), dict):
            txn_amount = eb_dict["transaction"].get("amount")
            if txn_amount:
                trusted = txn_amount
                trusted_source = "transaction.amount"
        # Priority 3: reconciliation.bank_amount
        elif isinstance(eb_dict.get("reconciliation_status"), dict):
            bank_amt = eb_dict["reconciliation_status"].get("bank_amount")
            if bank_amt:
                trusted = bank_amt
                trusted_source = "reconciliation.bank_amount"

    # Determine mismatch
    has_mismatch = False
    mismatch_desc = ""
    if claimed is not None and trusted is not None and claimed != trusted:
        has_mismatch = True
        mismatch_desc = (
            f"Khách khai {claimed:,}đ, nhưng hệ thống chỉ ghi nhận "
            f"{trusted:,}đ (từ {trusted_source}). "
            f"Mọi action xử lý tiền chỉ dùng số tiền {trusted:,}đ từ hệ thống. "
            f"Nhân viên cần xác minh phần chênh lệch trước khi duyệt."
        )

    return AmountVerification(
        customer_claimed_amount=claimed,
        trusted_amount=trusted,
        trusted_amount_source=trusted_source,
        action_amount=trusted,  # Action always uses trusted
        action_amount_source=trusted_source,
        has_amount_mismatch=has_mismatch,
        mismatch_description=mismatch_desc,
    )


def build_resolution_ticket(
    state: dict[str, Any],
    generated_response: GeneratedResponse | None = None,
) -> ResolutionTicket:
    """Build a complete resolution ticket from state + LLM response.

    The ticket is built DETERMINISTICALLY:
    - Actions come from rule engine mapping (not LLM)
    - MCP tools come from static lookup table
    - Resolution status is computed from evidence + action
    - Staff instruction comes from template

    The LLM's generated_response is used ONLY for:
    - issue_summary (from case_summary)
    - problem_location, problem_explanation
    - customer_reply_draft
    - evidence_checked (supplemented by our own computation)
    - safety_notes

    Args:
        state: Full case state dict.
        generated_response: Optional LLM-generated response for text fields.

    Returns:
        Complete ResolutionTicket.
    """
    case_id = state.get("case_id", "")
    workflow = state.get("selected_workflow") or "unknown"
    action_type = _extract_action_type(state)
    approval_required = _extract_approval_required(state)
    risk_level = _extract_risk_level(state)
    diagnosis = _extract_diagnosis(state)
    has_conflict = state.get("has_conflict", False)

    # Approval status
    approval_status_raw = state.get("approval_status")
    approval_status = None
    if approval_status_raw is not None:
        approval_status = (
            approval_status_raw.value
            if hasattr(approval_status_raw, "value")
            else str(approval_status_raw)
        )

    case_status_raw = state.get("status")
    case_status = None
    if case_status_raw is not None:
        case_status = (
            case_status_raw.value
            if hasattr(case_status_raw, "value")
            else str(case_status_raw)
        )

    # ── Amount verification ──
    amount_verification = _build_amount_verification(state)

    # ── Evidence analysis ──
    evidence_checked, missing_evidence = _compute_evidence_checked_and_missing(state)

    # ── Amount mismatch escalation for money actions ──
    amount_mismatch_escalation = (
        amount_verification.has_amount_mismatch
        and action_type in _MONEY_ACTION_TYPES
    )

    # ── Claim verification ──
    claim_verification: ClaimVerificationSummary | None = state.get(
        "claim_verification_summary",
    )
    has_system_evidence_conflict = (
        claim_verification.has_system_evidence_conflict
        if claim_verification
        else False
    )

    # ── Resolution status ──
    # System evidence conflict forces manual_review_required
    resolution_status = _determine_resolution_status(
        action_type,
        missing_evidence,
        has_conflict or amount_mismatch_escalation or has_system_evidence_conflict,
    )

    # ── Build recommended actions ──
    actions: list[TicketAction] = []
    if action_type:
        mapping = _ACTION_MAP.get(action_type, {
            "action_name": action_type.replace("_", " ").title(),
            "description": "",
            "mcp_tool": None,
            "execution_mode": "manual",
            "preconditions": [],
            "evidence_dependencies": [],
            "expected_result": "",
            "safety_notes": [],
        })

        status = _determine_action_status(
            action_type, approval_required, approval_status, case_status,
        )

        # Compute approval_status for action
        if not approval_required:
            action_approval = "not_required"
        elif approval_status == "approved":
            action_approval = "approved"
        elif approval_status == "rejected":
            action_approval = "rejected"
        else:
            action_approval = "pending"

        # Build mcp_input from state evidence
        mcp_input = _build_mcp_input(state, action_type)

        # Per-action staff instruction (workflow-aware)
        selected_workflow = state.get("selected_workflow")
        if selected_workflow == "fraud_account_lock" and action_type in _FRAUD_STAFF_INSTRUCTIONS:
            action_staff_instruction = _FRAUD_STAFF_INSTRUCTIONS[action_type]
        else:
            action_staff_instruction = _STAFF_INSTRUCTIONS.get(
                action_type, _DEFAULT_STAFF_INSTRUCTION,
            )

        actions.append(TicketAction(
            action_id=f"{case_id}:{action_type}",
            action_name=mapping["action_name"],
            action_type=action_type,
            description=mapping.get("description", ""),
            mcp_tool=mapping["mcp_tool"],
            mcp_input=mcp_input,
            preconditions=mapping.get("preconditions", []),
            evidence_dependencies=mapping.get("evidence_dependencies", []),
            requires_approval=approval_required,
            approval_status=action_approval,
            execution_mode=mapping["execution_mode"],
            risk_level=risk_level,
            reason=diagnosis or "Rule engine recommendation",
            status=status,
            expected_result=mapping.get("expected_result", ""),
            safety_notes=mapping.get("safety_notes", []),
            staff_instruction=action_staff_instruction,
        ))

    # ── Staff instruction (workflow-aware) ──
    selected_workflow = state.get("selected_workflow")
    if selected_workflow == "fraud_account_lock" and (action_type or "") in _FRAUD_STAFF_INSTRUCTIONS:
        staff_instruction = _FRAUD_STAFF_INSTRUCTIONS[action_type or ""]
    else:
        staff_instruction = _STAFF_INSTRUCTIONS.get(
            action_type or "", _DEFAULT_STAFF_INSTRUCTION,
        )
    staff_instruction += _build_missing_evidence_instruction(missing_evidence)

    # ── Pull text from LLM response (if available) ──
    if generated_response:
        issue_summary = generated_response.case_summary
        problem_location = generated_response.problem_location
        problem_explanation = generated_response.problem_explanation
        customer_reply_draft = generated_response.customer_reply_draft
        safety_notes = list(generated_response.safety_notes)
        # Merge evidence_checked: LLM's + our computed ones
        llm_checked = list(generated_response.evidence_checked)
        for ec in evidence_checked:
            if ec not in llm_checked:
                llm_checked.append(ec)
        evidence_checked = llm_checked
    else:
        issue_summary = "Hệ thống đã ghi nhận khiếu nại và phân tích evidence."
        problem_location = "unknown"
        problem_explanation = "Chưa có tổng hợp AI. Nhân viên cần kiểm tra evidence thủ công."
        customer_reply_draft = (
            "Dạ em đã ghi nhận khiếu nại của anh/chị. Bộ phận phụ trách sẽ kiểm tra "
            "và cập nhật kết quả sớm nhất."
        )
        safety_notes = [
            "Không tự động thực hiện action ảnh hưởng tiền hoặc tài khoản.",
            "Draft action cần phê duyệt trước khi thực hiện.",
        ]

    # Always add risk-related safety note for high/critical
    if risk_level in ("high", "critical"):
        risk_note = f"⚠ Risk level = {risk_level}. Cần phê duyệt cấp cao trước khi thực hiện."
        if risk_note not in safety_notes:
            safety_notes.append(risk_note)

    # Amount mismatch safety note
    if amount_verification.has_amount_mismatch:
        mismatch_note = (
            f"⚠ Chênh lệch số tiền: khách khai "
            f"{amount_verification.customer_claimed_amount:,}đ, "
            f"hệ thống ghi nhận {amount_verification.trusted_amount:,}đ "
            f"(từ {amount_verification.trusted_amount_source}). "
            f"Cần xác minh trước khi duyệt."
        )
        if mismatch_note not in safety_notes:
            safety_notes.append(mismatch_note)

    # ── Claim verification safety notes ──
    if claim_verification and claim_verification.has_customer_detail_mismatch:
        cv_note = (
            "⚠ Thông tin khách cung cấp có điểm lệch. "
            "Agent sẽ xử lý theo dữ liệu chuẩn của hệ thống."
        )
        if cv_note not in safety_notes:
            safety_notes.append(cv_note)

    if claim_verification and claim_verification.has_system_evidence_conflict:
        sys_note = (
            "🚨 Các nguồn dữ liệu hệ thống đang mâu thuẫn. "
            "Cần kiểm tra thủ công trước khi tạo action rủi ro."
        )
        if sys_note not in safety_notes:
            safety_notes.append(sys_note)
        # Override resolution status for system conflict
        resolution_status = "manual_review_required"

    logger.info(
        "[TicketBuilder] Built ticket: case=%s, workflow=%s, action=%s, "
        "resolution=%s, actions_count=%d, missing_evidence=%d, "
        "amount_mismatch=%s, claim_verification=%s",
        case_id, workflow, action_type,
        resolution_status, len(actions), len(missing_evidence),
        amount_verification.has_amount_mismatch,
        bool(claim_verification),
    )

    return ResolutionTicket(
        ticket_id=case_id,
        ticket_type=workflow,
        issue_summary=issue_summary,
        problem_location=problem_location,
        problem_explanation=problem_explanation,
        evidence_checked=evidence_checked,
        missing_evidence=missing_evidence,
        resolution_status=resolution_status,
        recommended_actions=actions,
        staff_instruction=staff_instruction,
        customer_reply_draft=customer_reply_draft,
        safety_notes=safety_notes,
        amount_verification=amount_verification,
        claim_verification=claim_verification,
    )
