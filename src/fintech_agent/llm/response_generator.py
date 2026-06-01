"""LLM response generator — dynamic case summary for CS/Ops staff.

This service uses OpenAI to summarize case evidence + rule decisions into
human-readable text for customer service / operations staff.

SAFETY INVARIANTS (non-negotiable):
  - LLM generates text/explanation ONLY.
  - LLM MUST NOT change recommended_action, risk_level, or approval_required.
  - LLM MUST NOT approve cases, execute refunds, unlock accounts, or modify ledger.
  - If action is draft/pending approval, output MUST say so explicitly.
  - MUST NOT leak internal rules, fraud thresholds, API keys, stack traces.
  - MUST NOT ask customer for OTP, password, PIN, private key.
  - Graph MUST NOT fail if LLM errors — always fallback to safe generic response.

EVIDENCE-FIRST RULE:
  - problem_location MUST be determined ONLY from structured_evidence.
  - raw_complaint is background context only — NOT evidence.
  - If structured evidence is insufficient, return problem_location = "unknown".
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from fintech_agent.schemas.response_generation import GeneratedResponse, ResponseDebug

logger = logging.getLogger(__name__)

# ─── System Prompt ──────────────────────────────────────────────

SYSTEM_PROMPT = """\
Bạn là AI hỗ trợ nhân viên CS/Ops trong hệ thống ví điện tử/fintech.

Nhiệm vụ:
- Đọc structured evidence (nguồn dữ liệu thực tế từ MCP tools) và rule decision (quyết định từ Rule Engine).
- Tổng hợp thành câu trả lời dễ hiểu cho nhân viên.
- Xác định problem_location DỰA TRÊN structured_evidence, KHÔNG từ raw_complaint.
- Giải thích evidence nào đã được kiểm tra và evidence nào hỗ trợ kết luận.
- Đề xuất bước tiếp theo cho nhân viên dựa trên recommended_action có sẵn.
- Viết câu trả lời nháp cho khách hàng.

═══════════════════════════════════════════════════════════════
QUY TẮC BẮT BUỘC VỀ EVIDENCE VÀ PROBLEM_LOCATION:
═══════════════════════════════════════════════════════════════

1. raw_complaint là lời khách hàng — KHÔNG PHẢI evidence.
   - Dùng raw_complaint CHỈ để hiểu bối cảnh ban đầu.
   - KHÔNG được kết luận problem_location chỉ từ raw_complaint.
   - Khách có thể nói sai, hiểu sai, hoặc mô tả không chính xác.

2. structured_evidence là nguồn sự thật duy nhất:
   - transaction data, wallet ledger, provider status, bank reconciliation,
     refund status, account status, fraud case — đây là evidence thực tế.
   - CHỈ dùng structured_evidence để xác định problem_location.

3. rule_decision là quyết định deterministic từ Rule Engine:
   - recommended_action, risk_level, approval_required, diagnosis.
   - KHÔNG ĐƯỢC thay đổi các giá trị này.
   - Chỉ giải thích và truyền đạt cho nhân viên.

4. Nếu structured_evidence thiếu hoặc không đủ để kết luận:
   - problem_location = "unknown"
   - problem_location_confidence = "unknown"
   - Giải thích evidence nào đang thiếu.
   - recommended_next_step = yêu cầu nhân viên xác minh evidence thiếu.

═══════════════════════════════════════════════════════════════
QUY TẮC XÁC ĐỊNH PROBLEM_LOCATION (từ evidence):
═══════════════════════════════════════════════════════════════

- wallet_system: ví/ledger/transaction nội bộ lệch trạng thái
  Ví dụ: bank xác nhận thành công + tiền vào master wallet + nhưng ví user vẫn pending
- bank: ngân hàng chưa xác nhận hoặc tiền chưa vào tài khoản tổng
  Ví dụ: bank status = failed, hoặc money_received_in_master_wallet = false
- provider: nhà cung cấp dịch vụ chưa cấp dịch vụ/chưa xác nhận thanh toán
  Ví dụ: transaction completed + wallet debited + provider status = not_confirmed
- reconciliation: dữ liệu đối soát thiếu hoặc mâu thuẫn giữa các hệ thống
  Ví dụ: conflict giữa ledger và transaction, hoặc reconciliation mismatch
- fraud_system: tài khoản bị khóa do risk/fraud signal
  Ví dụ: account_status = locked, fraud_case có risk_score cao
- customer_input: thiếu thông tin cần thiết từ khách (mã giao dịch, giấy tờ)
- unknown: CHỈ khi structured evidence thật sự thiếu hoặc mâu thuẫn không thể kết luận

═══════════════════════════════════════════════════════════════
QUY TẮC MỨC TIN CẬY (problem_location_confidence):
═══════════════════════════════════════════════════════════════

- "high": nhiều structured evidence fields đồng nhất hỗ trợ cùng kết luận
- "medium": một số evidence hỗ trợ nhưng thiếu 1 nguồn xác nhận
- "low": complaint gợi ý vấn đề nhưng structured evidence chưa đủ rõ ràng
- "unknown": không có structured evidence đáng tin cậy

═══════════════════════════════════════════════════════════════
RÀNG BUỘC AN TOÀN:
═══════════════════════════════════════════════════════════════

- Không được thay đổi recommended_action, risk_level, approval_required.
- Không được tự approve case, execute refund, force-success, unlock account, sửa ledger.
- Nếu action chỉ là draft/chờ phê duyệt, phải nói rõ đó là draft/chờ phê duyệt.
- Không nói tiền đã được cộng, refund đã thực hiện, tài khoản đã mở khóa
  nếu evidence chỉ cho thấy action đang là draft hoặc chờ duyệt.
- Không tiết lộ rule nội bộ, threshold fraud/risk, hoặc thông tin nhạy cảm.
- Không yêu cầu khách cung cấp OTP, mật khẩu, PIN, private key.
- Nếu evidence thiếu hoặc mâu thuẫn, nói cần manual review/xác minh thêm.

═══════════════════════════════════════════════════════════════
QUY TẮC VỀ SỐ TIỀN VÀ DỮ LIỆU KHÁCH CUNG CẤP:
═══════════════════════════════════════════════════════════════

- Số tiền khách hàng khai trong khiếu nại CHỈ LÀ THAM KHẢO.
- Số tiền xử lý (refund, force success, reconciliation) PHẢI lấy từ hệ thống:
  ưu tiên wallet_ledger.debit_amount > transaction.amount > reconciliation.bank_amount
- Nếu số tiền khách khai KHÁC số tiền hệ thống, PHẢI nêu rõ trong case_summary:
  "Khách khai Xđ, hệ thống ghi nhận Yđ. Số tiền xử lý = Yđ (từ [nguồn])."
- Dữ liệu khách cung cấp chỉ là thông tin tham khảo. Số liệu xử lý lấy từ hệ thống.
- KHÔNG BAO GIỜ dùng amount_claimed từ extracted_info làm số tiền cho refund/force-success.
- Nếu có chênh lệch, recommend nhân viên xác minh trước khi duyệt.

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT (JSON hợp lệ):
═══════════════════════════════════════════════════════════════

{
  "case_summary": string,
  "problem_location": string,
  "problem_explanation": string,
  "evidence_checked": string[],
  "evidence_supporting_problem_location": string[],
  "problem_location_confidence": "high" | "medium" | "low" | "unknown",
  "internal_summary": string,
  "recommended_next_step": string,
  "customer_reply_draft": string,
  "safety_notes": string[]
}"""


# ─── Sensitive Field Blocklist ──────────────────────────────────

_BLOCKED_KEYS = frozenset({
    "openai_api_key", "api_key", "secret", "token", "password",
    "pin", "otp", "private_key", "supabase_key", "database_url",
    "stack_trace", "traceback",
})


def _is_safe_key(key: str) -> bool:
    """Return False if key looks like it contains sensitive data."""
    lower = key.lower()
    return not any(blocked in lower for blocked in _BLOCKED_KEYS)


# ─── Context Builder ────────────────────────────────────────────


def build_response_context(state: dict[str, Any]) -> dict[str, Any]:
    """Extract safe fields from state for LLM context.

    Structures context into clearly separated sections so the LLM
    cannot confuse raw complaint text with structured evidence.

    Returns a dict with:
      - raw_complaint: background context only (NOT evidence)
      - structured_evidence: source of truth for problem_location
      - rule_decision: deterministic decision (read-only)
      - case_metadata: workflow, conflicts, tool errors
      - safety_boundaries: always-included safety reminders
    """
    context: dict[str, Any] = {}

    # ── Raw complaint (background only, NOT evidence) ──
    raw = state.get("raw_complaint")
    if raw:
        context["raw_complaint"] = raw

    # ── Extracted info (from complaint — metadata, not evidence) ──
    ei = state.get("extracted_info")
    if ei is not None:
        if hasattr(ei, "model_dump"):
            ei = ei.model_dump(mode="json", exclude_none=True)
        context["extracted_info"] = ei

    # ── Structured evidence (SOURCE OF TRUTH) ──
    structured_evidence: dict[str, Any] = {}

    eb = state.get("evidence_bundle") or state.get("evidence")
    if eb is not None:
        if hasattr(eb, "model_dump"):
            eb_dict = eb.model_dump(mode="json", exclude_none=True)
        elif isinstance(eb, dict):
            eb_dict = eb
        else:
            eb_dict = {}

        # Remove internal fields not useful for LLM
        eb_dict.pop("tool_errors", None)
        eb_dict.pop("conflicts", None)

        # Normalize provider names for LLM clarity
        if "train_provider" in eb_dict:
            eb_dict["provider_status"] = eb_dict.pop("train_provider")
        if "utility_provider" in eb_dict:
            eb_dict["provider_status"] = eb_dict.pop("utility_provider")

        structured_evidence = eb_dict

    context["structured_evidence"] = structured_evidence

    # ── Conflicts (evidence-level, important for diagnosis) ──
    conflicts = state.get("conflicts", [])
    if conflicts:
        context["conflicts"] = [
            c.model_dump(mode="json") if hasattr(c, "model_dump") else str(c)
            for c in conflicts
        ]

    # ── Rule decision (deterministic, read-only) ──
    rule_decision: dict[str, Any] = {}

    action = state.get("recommended_action")
    if action is not None:
        if hasattr(action, "action_type"):
            rule_decision["recommended_action"] = action.action_type.value
        elif isinstance(action, str):
            rule_decision["recommended_action"] = action
        else:
            rule_decision["recommended_action"] = str(action)

        if hasattr(action, "risk_level"):
            rl = action.risk_level
            rule_decision["risk_level"] = rl.value if hasattr(rl, "value") else str(rl)
        if hasattr(action, "approval_required"):
            rule_decision["approval_required"] = action.approval_required
        if hasattr(action, "diagnosis"):
            rule_decision["diagnosis"] = action.diagnosis
    else:
        # Fallback to top-level state fields
        for key in ("recommended_action", "risk_level", "approval_required", "diagnosis"):
            val = state.get(key)
            if val is not None:
                if hasattr(val, "value"):
                    rule_decision[key] = val.value
                else:
                    rule_decision[key] = val

    approval_status = state.get("approval_status")
    if approval_status is not None:
        rule_decision["approval_status"] = (
            approval_status.value
            if hasattr(approval_status, "value")
            else str(approval_status)
        )

    context["rule_decision"] = rule_decision

    # ── Case metadata ──
    wf = state.get("selected_workflow")
    if wf:
        context["selected_workflow"] = wf

    # ── Tool errors for context ──
    eb_raw = state.get("evidence_bundle")
    if eb_raw is not None and hasattr(eb_raw, "tool_errors"):
        errors = eb_raw.tool_errors
        if errors:
            context["tool_errors"] = errors

    # ── Safety boundaries (always include) ──
    context["safety_boundaries"] = [
        "Không tự động thực hiện action ảnh hưởng tiền hoặc tài khoản",
        "Draft action cần phê duyệt trước khi thực hiện",
        "Không sửa ledger, không force-success, không unlock account tự động",
        "Số tiền xử lý chỉ lấy từ hệ thống (wallet_ledger/transaction), không dùng số tiền khách khai",
    ]

    # ── Amount verification context ──
    ei_raw = state.get("extracted_info")
    claimed_amount = None
    if ei_raw is not None:
        if hasattr(ei_raw, "amount_claimed"):
            claimed_amount = ei_raw.amount_claimed
        elif isinstance(ei_raw, dict):
            claimed_amount = ei_raw.get("amount_claimed")

    if claimed_amount is not None:
        # Compute trusted amount from evidence for LLM context
        trusted_amount = None
        trusted_source = None
        eb_raw2 = state.get("evidence_bundle") or state.get("evidence")
        if eb_raw2 is not None:
            if hasattr(eb_raw2, "model_dump"):
                eb_d = eb_raw2.model_dump(mode="json", exclude_none=True)
            elif isinstance(eb_raw2, dict):
                eb_d = eb_raw2
            else:
                eb_d = {}
            wl2 = eb_d.get("wallet_ledger")
            if isinstance(wl2, dict) and wl2.get("debit_amount"):
                trusted_amount = wl2["debit_amount"]
                trusted_source = "wallet_ledger.debit_amount"
            elif isinstance(eb_d.get("transaction"), dict):
                ta = eb_d["transaction"].get("amount")
                if ta:
                    trusted_amount = ta
                    trusted_source = "transaction.amount"

        context["amount_verification"] = {
            "customer_claimed_amount": claimed_amount,
            "trusted_system_amount": trusted_amount,
            "trusted_source": trusted_source,
            "has_mismatch": (
                trusted_amount is not None
                and claimed_amount != trusted_amount
            ),
            "rule": "Số tiền xử lý chỉ được lấy từ hệ thống, không dùng số khách khai.",
        }

    # Final filter: remove any accidentally included sensitive keys
    context = {k: v for k, v in context.items() if _is_safe_key(k)}

    return context


# ─── Safe Fallback ──────────────────────────────────────────────


def generate_safe_fallback_response(
    state: dict[str, Any],
    reason: str = "LLM không khả dụng",
    llm_error: str | None = None,
) -> GeneratedResponse:
    """Generate a generic safe response when LLM is unavailable or fails.

    Workflow-aware: produces richer explanations for fraud_account_lock
    when evidence is available.
    """
    selected_workflow = state.get("selected_workflow")

    # ── Fraud-specific fallback ──
    if selected_workflow == "fraud_account_lock":
        return _generate_fraud_fallback(state, reason, llm_error)

    # ── Generic fallback ──
    return GeneratedResponse(
        case_summary=(
            "Hệ thống đã ghi nhận khiếu nại và trích xuất thông tin liên quan từ case."
        ),
        problem_location="unknown",
        problem_explanation=(
            "Không đủ thông tin từ structured evidence để xác định nguyên nhân gốc. "
            "Nhân viên cần kiểm tra lại evidence trước khi kết luận."
        ),
        evidence_checked=[],
        evidence_supporting_problem_location=[],
        problem_location_confidence="unknown",
        internal_summary=(
            f"LLM response generation fallback. Lý do: {reason}"
        ),
        recommended_next_step=(
            "Nhân viên xem lại evidence, action đề xuất và trạng thái phê duyệt "
            "trước khi xử lý."
        ),
        customer_reply_draft=(
            "Dạ em đã ghi nhận khiếu nại của anh/chị. Bộ phận phụ trách sẽ kiểm tra "
            "thông tin và cập nhật kết quả sau khi hoàn tất xác minh nội bộ."
        ),
        safety_notes=[
            "Không tự động thực hiện action ảnh hưởng tiền hoặc tài khoản.",
            "Nếu action cần phê duyệt, phải chờ nhân viên phê duyệt trước khi xử lý tiếp.",
        ],
        debug=ResponseDebug(
            generation_mode="fallback",
            fallback_reason=reason,
            llm_error=llm_error,
            model_used=None,
        ),
    )


def _generate_fraud_fallback(
    state: dict[str, Any],
    reason: str,
    llm_error: str | None,
) -> GeneratedResponse:
    """Fraud-specific fallback response with rich evidence-based explanation."""
    eb = state.get("evidence_bundle") or state.get("evidence")
    evidence_checked: list[str] = []
    supporting: list[str] = []

    # Extract fraud evidence details
    account_status_val = None
    withdrawal_val = None
    lock_reason_val = None
    risk_score = None
    risk_level = None
    fraud_status = None
    recommended_decision = None

    if eb is not None:
        if hasattr(eb, "account_status") and eb.account_status:
            acct = eb.account_status
            account_status_val = acct.account_status
            withdrawal_val = acct.withdrawal_enabled
            lock_reason_val = acct.lock_reason
            evidence_checked.extend([
                "Trạng thái tài khoản",
                "Trạng thái rút tiền",
            ])
            supporting.append(f"account_status={account_status_val}")
            supporting.append(f"withdrawal_enabled={withdrawal_val}")
            if lock_reason_val:
                supporting.append(f"lock_reason={lock_reason_val}")

        if hasattr(eb, "fraud_case") and eb.fraud_case:
            fc = eb.fraud_case
            risk_score = fc.risk_score
            risk_level = fc.risk_level
            fraud_status = fc.fraud_status
            recommended_decision = fc.recommended_decision
            evidence_checked.extend([
                "Mức rủi ro / Điểm rủi ro",
                "Trạng thái fraud",
                "Kết quả rà soát fraud",
            ])
            if risk_score is not None:
                supporting.append(f"risk_score={risk_score}")
            if risk_level:
                supporting.append(f"risk_level={risk_level}")
            if fraud_status:
                supporting.append(f"fraud_status={fraud_status}")
            if recommended_decision:
                supporting.append(f"recommended_decision={recommended_decision}")

            # Device/login signals
            if fc.device_events:
                evidence_checked.append("Tín hiệu thiết bị/đăng nhập")
            if fc.recent_transactions:
                evidence_checked.append("Giao dịch đáng ngờ gần đây")

    # ── Detect identity lookup failure ──
    # If neither account_status nor fraud_case exist, identity was not resolved
    identity_not_found = (account_status_val is None and fraud_status is None)

    # Extract phone from extracted_info for the explanation
    ei = state.get("extracted_info")
    ei_phone = None
    if ei is not None:
        if hasattr(ei, "model_dump"):
            ei_phone = getattr(ei, "phone", None)
        elif isinstance(ei, dict):
            ei_phone = ei.get("phone")

    # Build problem explanation based on evidence
    if identity_not_found and ei_phone:
        problem_explanation = (
            f"Không tìm thấy tài khoản khớp với số điện thoại "
            f"{ei_phone} trong hệ thống. "
            "Vì chưa định danh được tài khoản, agent chưa thể "
            "xác minh trạng thái khóa, trạng thái rút tiền "
            "hoặc dữ liệu Risk/Fraud. "
            "Cần yêu cầu khách kiểm tra lại số điện thoại/email/"
            "wallet_id hoặc cung cấp mã giao dịch gần nhất."
        )
        confidence = "low"
    elif identity_not_found:
        problem_explanation = (
            "Chưa định danh được tài khoản khách hàng. "
            "Agent chưa thể xác minh trạng thái khóa, "
            "trạng thái rút tiền hoặc dữ liệu Risk/Fraud. "
            "Cần yêu cầu khách cung cấp số điện thoại/email/"
            "wallet_id hoặc mã giao dịch gần nhất."
        )
        confidence = "low"
    else:
        is_false_positive = (
            risk_level in ("low", "medium")
            or recommended_decision in ("unlock", "false_positive_candidate", "review_needed")
        )

        if is_false_positive:
            problem_explanation = (
                "Hệ thống Fraud Detection đã khóa tài khoản, khiến khách không thể rút tiền. "
                "Tuy nhiên dữ liệu rủi ro hiện ở mức thấp"
                + (f" (risk_score={risk_score})" if risk_score is not None else "")
                + ", KYC hợp lệ và không có tín hiệu giao dịch bất thường nghiêm trọng. "
                "Vì vậy case này có khả năng là false positive. "
                "Agent đề xuất tạo bản nháp mở khóa tài khoản, "
                "cần Risk/Ops phê duyệt trước khi thực hiện."
            )
            confidence = "high"
        elif risk_level == "high":
            problem_explanation = (
                "Hệ thống ghi nhận nhiều tín hiệu rủi ro ở mức cao. "
                "Không đề xuất mở khóa tài khoản. Nhân viên cần giữ trạng thái khóa, "
                "yêu cầu khách bổ sung chứng từ xác minh "
                "và chuyển Risk/Fraud review."
            )
            confidence = "high"
        else:
            # Missing evidence or ambiguous risk
            problem_explanation = (
                "Hệ thống Fraud Detection đã khóa tài khoản. "
                "Tuy nhiên dữ liệu fraud evidence chưa đầy đủ để kết luận"
                + (f" (risk_level={risk_level})" if risk_level else "")
                + ". Cần kiểm tra thêm fraud case, risk signals, "
                "lịch sử giao dịch, thiết bị đăng nhập và KYC trước khi quyết định."
            )
            confidence = "medium"

    case_summary = (
        "Khách hàng khiếu nại tài khoản bị khóa và không thể rút tiền. "
        + (f"Số điện thoại: {ei_phone}. " if ei_phone else "")
        + f"Trạng thái tài khoản: {account_status_val or 'chưa xác định'}. "
        + f"Mức rủi ro: {risk_level or 'chưa xác định'}."
    )

    # Action-specific next step
    action = state.get("recommended_action")
    action_type = None
    if action is not None:
        if hasattr(action, "action_type"):
            action_type = action.action_type.value if hasattr(action.action_type, "value") else str(action.action_type)
        elif isinstance(action, str):
            action_type = action

    if identity_not_found:
        next_step = (
            "Yêu cầu khách xác nhận lại số điện thoại đăng ký ví, "
            "email, wallet_id hoặc cung cấp mã giao dịch gần nhất. "
            "Sau khi định danh được tài khoản, nhân viên mới kiểm tra "
            "account_status, withdrawal_status, fraud_case, risk signals và KYC."
        )
    elif action_type == "create_unlock_account_draft":
        next_step = (
            "Kiểm tra fraud evidence và risk signals. "
            "Nếu xác nhận false positive, phê duyệt draft mở khóa tài khoản."
        )
    elif action_type == "create_request_documents_response_draft":
        next_step = (
            "Yêu cầu khách bổ sung giấy tờ xác minh. "
            "Tài khoản vẫn bị khóa trong thời gian xác minh."
        )
    else:
        next_step = (
            "Nhân viên kiểm tra toàn bộ fraud evidence, risk signals "
            "và lịch sử giao dịch trước khi quyết định."
        )

    # Customer reply — identity-not-found needs different wording
    if identity_not_found:
        customer_reply = (
            "Dạ em đã ghi nhận trường hợp của anh/chị. "
            "Tuy nhiên em cần xác nhận lại thông tin tài khoản. "
            "Anh/chị vui lòng kiểm tra lại số điện thoại đã đăng ký ví, "
            "email hoặc cung cấp mã giao dịch gần nhất để em tra cứu giúp ạ."
        )
    else:
        customer_reply = (
            "Dạ em đã ghi nhận trường hợp tài khoản của anh/chị bị khóa "
            "và không thể rút tiền. Bộ phận phụ trách sẽ kiểm tra "
            "thông tin bảo mật và cập nhật kết quả sau khi hoàn tất xác minh."
        )

    return GeneratedResponse(
        case_summary=case_summary,
        problem_location="fraud_system" if not identity_not_found else "identity_lookup",
        problem_explanation=problem_explanation,
        evidence_checked=evidence_checked,
        evidence_supporting_problem_location=supporting,
        problem_location_confidence=confidence,
        internal_summary=(
            f"Fraud account lock case. "
            f"Identity resolved: {not identity_not_found}. "
            f"Risk: {risk_level}, Score: {risk_score}, "
            f"Decision: {recommended_decision}. "
            f"Fallback: {reason}"
        ),
        recommended_next_step=next_step,
        customer_reply_draft=customer_reply,
        safety_notes=[
            "Không tự động unlock account.",
            "Không bỏ qua phê duyệt Risk/Ops.",
            "Không tiết lộ risk_score, fraud threshold hoặc rule nội bộ cho khách.",
        ],
        debug=ResponseDebug(
            generation_mode="fallback",
            fallback_reason=reason,
            llm_error=llm_error,
            model_used=None,
        ),
    )


# ─── LLM Call ───────────────────────────────────────────────────


def generate_response_with_llm(state: dict[str, Any]) -> GeneratedResponse:
    """Call OpenAI to generate a structured case response.

    Uses json_object response format for reliable parsing.
    Falls back to safe response on any error.

    The context is structured into clearly separated sections so the LLM
    can distinguish raw complaint (background) from structured evidence
    (source of truth) and rule decision (deterministic, read-only).
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key:
        logger.warning("[LLM] OPENAI_API_KEY not set — using fallback response")
        return generate_safe_fallback_response(
            state, reason="OPENAI_API_KEY is missing"
        )

    # Build sanitized context
    context = build_response_context(state)

    logger.info(
        "[LLM] Context built: keys=%s, evidence_keys=%s, context_size=%d chars",
        list(context.keys()),
        list(context.get("structured_evidence", {}).keys()),
        len(json.dumps(context, ensure_ascii=False, default=str)),
    )

    user_prompt = (
        "Hãy tổng hợp case sau cho nhân viên CS/Ops.\n\n"
        "QUY TẮC QUAN TRỌNG:\n"
        "- raw_complaint bên dưới là lời khách hàng — CHỈ dùng làm bối cảnh.\n"
        "- structured_evidence là nguồn sự thật DUY NHẤT để xác định problem_location.\n"
        "- rule_decision là quyết định deterministic — KHÔNG thay đổi.\n"
        "- Nếu structured_evidence thiếu, trả problem_location = 'unknown' "
        "và problem_location_confidence = 'unknown'.\n\n"
        "Dữ liệu case:\n"
        + json.dumps(context, ensure_ascii=False, default=str)
    )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, timeout=30.0)

        logger.info("[LLM] Calling OpenAI model=%s ...", model)

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )

        raw_content = response.choices[0].message.content
        if not raw_content:
            logger.warning("[LLM] OpenAI returned empty content — using fallback")
            return generate_safe_fallback_response(
                state, reason="OpenAI returned empty content"
            )

        parsed = json.loads(raw_content)

        # Normalize: fill missing list fields instead of full fallback
        if "evidence_checked" not in parsed:
            parsed["evidence_checked"] = []
        if "safety_notes" not in parsed:
            parsed["safety_notes"] = []
        if "evidence_supporting_problem_location" not in parsed:
            parsed["evidence_supporting_problem_location"] = []
        if "problem_location_confidence" not in parsed:
            parsed["problem_location_confidence"] = "unknown"

        # Validate confidence value
        valid_confidences = {"high", "medium", "low", "unknown"}
        if parsed["problem_location_confidence"] not in valid_confidences:
            parsed["problem_location_confidence"] = "unknown"

        # Check required fields
        required = ("case_summary", "problem_location", "problem_explanation")
        missing = [f for f in required if not parsed.get(f)]
        if missing:
            logger.warning(
                "[LLM] Missing required fields %s in LLM output — using fallback",
                missing,
            )
            return generate_safe_fallback_response(
                state,
                reason=f"LLM output missing required fields: {missing}",
                llm_error=raw_content[:500],
            )

        # Inject debug info (not from LLM output)
        parsed.pop("debug", None)
        result = GeneratedResponse(
            **parsed,
            debug=ResponseDebug(
                generation_mode="llm",
                fallback_reason=None,
                llm_error=None,
                model_used=model,
            ),
        )

        logger.info(
            "[LLM] Response generated: mode=llm, model=%s, location=%s, "
            "confidence=%s, supporting_evidence=%d, summary_len=%d",
            model,
            result.problem_location,
            result.problem_location_confidence,
            len(result.evidence_supporting_problem_location),
            len(result.case_summary),
        )
        return result

    except json.JSONDecodeError as exc:
        logger.error("[LLM] Invalid JSON from OpenAI: %s", exc)
        return generate_safe_fallback_response(
            state,
            reason="LLM returned invalid JSON",
            llm_error=str(exc),
        )
    except ImportError:
        logger.error("[LLM] openai package not installed — using fallback")
        return generate_safe_fallback_response(
            state, reason="openai package not installed"
        )
    except Exception as exc:
        # Catch-all: never let LLM errors crash the graph
        logger.error(
            "[LLM] Generation failed (%s): %s — using fallback",
            type(exc).__name__, exc,
        )
        return generate_safe_fallback_response(
            state,
            reason=f"LLM call failed: {type(exc).__name__}",
            llm_error=str(exc),
        )


# ─── Public Entry Point ─────────────────────────────────────────


def generate_case_response(state: dict[str, Any]) -> GeneratedResponse:
    """Generate a case response — LLM with safe fallback.

    This is the single entry point for response generation.
    The graph node should call this function.

    Logic:
      1. If OPENAI_API_KEY is set → call LLM.
      2. If LLM errors → fallback.
      3. If no key → fallback.
      4. Never raises — always returns valid GeneratedResponse.

    Args:
        state: Full case state dict (from CaseState.model_dump()).

    Returns:
        GeneratedResponse with case summary, explanation, and drafts.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")

    if not api_key:
        logger.info("[LLM] No OPENAI_API_KEY — generating fallback response")
        return generate_safe_fallback_response(
            state, reason="OPENAI_API_KEY is missing"
        )

    return generate_response_with_llm(state)
