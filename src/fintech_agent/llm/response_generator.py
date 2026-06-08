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

from fintech_agent.llm.diagnostic_engine import DiagnosticResult, diagnose
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


# ─── Diagnostic Helper ──────────────────────────────────────────


def _run_diagnostic(state: dict[str, Any]) -> DiagnosticResult:
    """Run DiagnosticEngine on state and return structured result."""
    workflow = state.get("selected_workflow") or "unknown"
    diagnosis = ""

    # Extract diagnosis from recommended_action or top-level state
    action = state.get("recommended_action")
    if action is not None:
        if hasattr(action, "diagnosis"):
            diagnosis = action.diagnosis or ""
        elif isinstance(action, dict):
            diagnosis = action.get("diagnosis", "")
        elif isinstance(action, str):
            diagnosis = action
    if not diagnosis:
        diagnosis = state.get("diagnosis", "")

    # Evidence bundle
    eb = state.get("evidence_bundle") or state.get("evidence")

    # Extracted info
    ei = state.get("extracted_info")
    ei_dict = None
    if ei is not None:
        if hasattr(ei, "model_dump"):
            ei_dict = ei.model_dump(mode="json", exclude_none=True)
        elif isinstance(ei, dict):
            ei_dict = ei

    return diagnose(workflow, diagnosis, eb, ei_dict)


def _diagnostic_to_response(
    diagnostic: DiagnosticResult,
    state: dict[str, Any],
    reason: str = "LLM không khả dụng",
    llm_error: str | None = None,
) -> GeneratedResponse:
    """Convert DiagnosticResult to GeneratedResponse.

    Builds a structured, data-driven response from the diagnostic engine
    instead of vague generic text.
    """
    workflow = state.get("selected_workflow") or "unknown"
    bn = diagnostic.bottleneck
    res = diagnostic.resolution

    # Build case summary from diagnostic + extracted info
    ei = state.get("extracted_info")
    ei_phone = None
    if ei is not None:
        if hasattr(ei, "phone"):
            ei_phone = ei.phone
        elif isinstance(ei, dict):
            ei_phone = ei.get("phone")

    if workflow == "fraud_account_lock":
        case_summary = (
            "Khách hàng khiếu nại tài khoản bị khóa và không thể rút tiền."
            + (f" Số điện thoại: {ei_phone}." if ei_phone else "")
        )
    elif workflow == "wallet_topup":
        case_summary = (
            "Khách hàng khiếu nại nạp tiền vào ví nhưng số dư chưa được cập nhật."
        )
    elif workflow == "merchant_settlement_delay":
        case_summary = (
            "Đối tác/merchant khiếu nại chưa nhận được tiền giải ngân "
            "theo chu kỳ thanh toán."
        )
    else:
        case_summary = (
            "Hệ thống đã ghi nhận khiếu nại và thu thập evidence."
        )

    # Next step from diagnostic resolution
    next_step_parts = res.next_steps_for_staff
    recommended_next_step = " → ".join(next_step_parts) if next_step_parts else (
        "Nhân viên xem lại evidence, action đề xuất và trạng thái phê duyệt."
    )

    # Customer reply
    if bn.location == "identity_lookup":
        customer_reply = (
            "Dạ em đã ghi nhận trường hợp của anh/chị. "
            "Tuy nhiên em cần xác nhận lại thông tin tài khoản. "
            "Anh/chị vui lòng kiểm tra lại số điện thoại đã đăng ký ví, "
            "email hoặc cung cấp mã giao dịch gần nhất để em tra cứu giúp ạ."
        )
    elif workflow == "fraud_account_lock":
        customer_reply = (
            "Dạ em đã ghi nhận trường hợp tài khoản của anh/chị bị khóa "
            "và không thể rút tiền. Bộ phận phụ trách sẽ kiểm tra "
            "thông tin bảo mật và cập nhật kết quả sau khi hoàn tất xác minh."
        )
    elif workflow == "wallet_topup":
        customer_reply = (
            "Dạ em đã ghi nhận trường hợp nạp tiền của anh/chị. "
            "Bộ phận kỹ thuật đang kiểm tra và sẽ cập nhật kết quả sớm nhất."
        )
    elif workflow == "merchant_settlement_delay":
        customer_reply = (
            "Chúng tôi đã ghi nhận yêu cầu về khoản thanh toán settlement. "
            "Đội ngũ Settlement đang kiểm tra và sẽ cập nhật kết quả "
            "sau khi hoàn tất xác minh nội bộ."
        )
    else:
        customer_reply = (
            "Dạ em đã ghi nhận khiếu nại của anh/chị. Bộ phận phụ trách sẽ kiểm tra "
            "thông tin và cập nhật kết quả sau khi hoàn tất xác minh nội bộ."
        )

    # Safety notes
    safety_notes = [
        "Không tự động thực hiện action ảnh hưởng tiền hoặc tài khoản.",
        "Nếu action cần phê duyệt, phải chờ nhân viên phê duyệt trước khi xử lý tiếp.",
    ]
    if workflow == "fraud_account_lock":
        safety_notes.extend([
            "Không tự động unlock account.",
            "Không tiết lộ risk_score, fraud threshold hoặc rule nội bộ cho khách.",
        ])
    elif workflow == "merchant_settlement_delay":
        safety_notes.extend([
            "Không tự động thực hiện payout.",
            "Số tiền payout lấy từ settlement_ledger, không từ merchant claim.",
            "Không tạo payout nếu bank account chưa verified.",
            "Không tạo payout trùng nếu payout đang processing/success.",
            "Manual payout cần phê duyệt.",
        ])

    return GeneratedResponse(
        case_summary=case_summary,
        problem_location=bn.location,
        problem_explanation=bn.explanation,
        evidence_checked=[],  # Will be enriched by ticket_builder
        evidence_supporting_problem_location=bn.evidence,
        problem_location_confidence=bn.confidence,
        internal_summary=(
            f"DiagnosticEngine fallback. Workflow={workflow}, "
            f"Bottleneck={bn.location}, Confidence={bn.confidence}, "
            f"Action={res.recommended_action}. Reason: {reason}"
        ),
        recommended_next_step=recommended_next_step,
        customer_reply_draft=customer_reply,
        safety_notes=safety_notes,
        missing_data=diagnostic.missing_data,
        debug=ResponseDebug(
            generation_mode="fallback",
            fallback_reason=reason,
            llm_error=llm_error,
            model_used=None,
        ),
    )


# ─── Safe Fallback ──────────────────────────────────────────────


def generate_safe_fallback_response(
    state: dict[str, Any],
    reason: str = "LLM không khả dụng",
    llm_error: str | None = None,
) -> GeneratedResponse:
    """Generate a diagnostic-driven safe response when LLM is unavailable.

    Uses the DiagnosticEngine to produce structured, data-driven explanations
    instead of vague generic text. All workflows are supported.
    """
    diagnostic = _run_diagnostic(state)
    return _diagnostic_to_response(diagnostic, state, reason, llm_error)


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
        if "missing_data" not in parsed:
            parsed["missing_data"] = []
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
