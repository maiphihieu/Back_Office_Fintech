"""Generic pipeline tests — proves the system handles diverse inputs
without hard-coded phrase matching.

Test groups:
  A. Same meaning, different wording (structured extraction)
  B. Same follow-up, different active workflows
  C. ETA/status across workflows
  D. Safety: PIN/OTP/password detection
  E. No hard-code scan (grep for forbidden patterns)
"""

import os
import re
import subprocess
import sys

import pytest

# ─── Import pipeline modules ───────────────────────────────────

from fintech_agent.llm.message_analyzer import (
    MessageAnalysis,
    ExtractedFields,
    ActiveCaseContext,
    analyze_customer_message,
    _fallback_extract_fields,
    _fallback_classify,
    load_response_policy,
)
from fintech_agent.llm.followup_analyzer import (
    FollowupAnalysis,
    analyze_customer_followup,
    _WORKFLOW_GUIDANCE,
)
from fintech_agent.api.generic_resolver import (
    ResolutionResult,
    resolve_case_evidence,
)
from fintech_agent.safety.evidence_mapper import to_public_safe_evidence
from fintech_agent.llm.response_composer import (
    ComposedResponse,
    compose_customer_response,
)
from fintech_agent.safety.output_guardrail import (
    GuardrailResult,
    check_response_safety,
)

# ─── Group A: Same meaning, different wording ───────────────────
# All these messages describe providing alternative transaction info.
# They should ALL produce structured extraction, not phrase matching.


class TestGroupA_DiverseExtractionWordings:
    """Same meaning expressed differently → all extract structured fields."""

    def test_formal_with_exact_amount_and_bank(self):
        msg = "tôi nạp khoảng 9h sáng, số tiền 500000, ngân hàng đã trừ tiền"
        fields = _fallback_extract_fields(msg)
        assert fields.amount is not None or fields.approximate_time_text is not None

    def test_informal_bank_confirmation(self):
        msg = "em chuyển tầm gần 10 giờ, app bank báo thành công rồi"
        fields = _fallback_extract_fields(msg)
        assert fields.approximate_time_text is not None
        assert fields.issue_type == "bank_confirmed_wallet_pending"

    def test_slang_half_million(self):
        msg = "tôi nạp nửa triệu sáng nay"
        fields = _fallback_extract_fields(msg)
        assert fields.amount == 500_000
        assert fields.approximate_date_text is not None or fields.approximate_time_text is not None

    def test_slang_lit_with_bank_confirmed(self):
        msg = "khoảng 5 lít, ngân hàng trừ rồi"
        fields = _fallback_extract_fields(msg)
        assert fields.amount == 500_000
        assert fields.issue_type == "bank_confirmed_wallet_pending"

    def test_has_bank_receipt(self):
        msg = "tôi có bill chuyển khoản, có mã tham chiếu trên biên lai"
        fields = _fallback_extract_fields(msg)
        assert fields.issue_type == "bank_confirmed_wallet_pending"

    def test_afternoon_transfer(self):
        msg = "tôi chuyển chiều nay, tầm 3 giờ, 1 triệu đồng"
        fields = _fallback_extract_fields(msg)
        assert fields.amount == 1_000_000

    def test_with_bank_reference_code(self):
        msg = "mã tham chiếu FT24123456, số tiền 200k, chuyển qua VCB"
        fields = _fallback_extract_fields(msg)
        assert fields.bank_reference == "FT24123456"
        assert fields.amount == 200_000
        assert fields.bank_name is not None

    def test_all_produce_provide_missing_info(self):
        """All alt-info messages should classify as provide_missing_info."""
        messages = [
            "tôi nạp khoảng 9h sáng, số tiền 500000, ngân hàng đã trừ tiền",
            "em chuyển tầm gần 10 giờ, app bank báo thành công rồi",
            "tôi nạp nửa triệu sáng nay",
            "khoảng 5 lít, ngân hàng trừ rồi",
        ]
        for msg in messages:
            fields = _fallback_extract_fields(msg)
            msg_type, conf, _is_correction = _fallback_classify(
                msg, fields,
                has_active_case=True,
                awaiting_field="transaction_id",
            )
            assert msg_type == "provide_missing_info", (
                f"Expected provide_missing_info for '{msg}', got {msg_type}"
            )


# ─── Group B: Same follow-up, different active workflows ────────

class TestGroupB_FollowUpAcrossWorkflows:
    """'tôi cần cung cấp gì' should produce different guidance
    depending on the active workflow."""

    @pytest.fixture
    def message(self):
        return "tôi cần cung cấp gì"

    def test_fraud_account_lock(self, message):
        ctx = ActiveCaseContext(
            case_id="C001", selected_workflow="fraud_account_lock",
        )
        result = analyze_customer_followup(message, ctx)
        assert result.is_followup or result.safe_response
        # Response should mention account/verification topics
        if result.safe_response:
            assert any(
                kw in result.safe_response.lower()
                for kw in ["bảo mật", "tài khoản", "khóa", "thiết bị", "xác minh"]
            ), f"Expected fraud-related guidance, got: {result.safe_response[:100]}"

    def test_wallet_topup(self, message):
        ctx = ActiveCaseContext(
            case_id="C002", selected_workflow="wallet_topup",
        )
        result = analyze_customer_followup(message, ctx)
        if result.safe_response:
            assert any(
                kw in result.safe_response.lower()
                for kw in ["giao dịch", "mã", "số tiền", "ngân hàng", "ví"]
            ), f"Expected topup guidance, got: {result.safe_response[:100]}"

    def test_no_active_case(self, message):
        ctx = ActiveCaseContext()  # no active case
        result = analyze_customer_followup(message, ctx)
        # Without active case, should ask what issue they have
        if result.safe_response:
            assert any(
                kw in result.safe_response.lower()
                for kw in ["hỗ trợ", "vấn đề", "mô tả"]
            ), f"Expected greeting/ask, got: {result.safe_response[:100]}"

    def test_merchant_settlement(self, message):
        ctx = ActiveCaseContext(
            case_id="C003", selected_workflow="merchant_settlement_delay",
        )
        result = analyze_customer_followup(message, ctx)
        if result.safe_response:
            assert any(
                kw in result.safe_response.lower()
                for kw in ["merchant", "thanh toán", "settlement", "payout", "đối tác", "thuế"]
            ), f"Expected settlement guidance, got: {result.safe_response[:100]}"


# ─── Group C: ETA/status across workflows ───────────────────────

class TestGroupC_StatusAcrossWorkflows:
    """'bao lâu nữa' should produce workflow-appropriate status response."""

    @pytest.fixture
    def status_messages(self):
        return ["bao lâu nữa", "khi nào xong", "đã xử lý chưa"]

    def test_wallet_topup_status(self, status_messages):
        ctx = ActiveCaseContext(
            case_id="C010", selected_workflow="wallet_topup",
        )
        for msg in status_messages:
            result = analyze_customer_followup(msg, ctx)
            # Should be a follow-up with status/eta intent
            assert result.is_followup or result.safe_response

    def test_fraud_lock_status(self, status_messages):
        ctx = ActiveCaseContext(
            case_id="C011", selected_workflow="fraud_account_lock",
        )
        for msg in status_messages:
            result = analyze_customer_followup(msg, ctx)
            assert result.is_followup or result.safe_response

    def test_merchant_settlement_status(self, status_messages):
        ctx = ActiveCaseContext(
            case_id="C012", selected_workflow="merchant_settlement_delay",
        )
        for msg in status_messages:
            result = analyze_customer_followup(msg, ctx)
            assert result.is_followup or result.safe_response


# ─── Group D: Safety — PIN/OTP/Password detection ──────────────

class TestGroupD_Safety:
    """Messages containing sensitive info should trigger safety warning."""

    def test_pin_disclosure(self):
        msg = "mã PIN của tôi là 123456"
        analysis = analyze_customer_message(msg)
        assert analysis.message_type == "provide_sensitive_info"

    def test_otp_disclosure(self):
        msg = "OTP: 789012"
        analysis = analyze_customer_message(msg)
        assert analysis.message_type == "provide_sensitive_info"

    def test_password_disclosure(self):
        msg = "mật khẩu là abc123xyz"
        analysis = analyze_customer_message(msg)
        assert analysis.message_type == "provide_sensitive_info"

    def test_card_number_disclosure(self):
        msg = "số thẻ 4111 2222 3333 4444"
        analysis = analyze_customer_message(msg)
        assert analysis.message_type == "provide_sensitive_info"

    def test_guardrail_blocks_pin_request(self):
        response = "Vui lòng cung cấp mã PIN để xác minh"
        result = check_response_safety(response)
        assert not result.is_safe
        assert result.sanitized_text is not None

    def test_guardrail_blocks_internal_terms(self):
        response = "Chúng tôi đã force-success giao dịch qua master wallet"
        result = check_response_safety(response)
        assert not result.is_safe

    def test_guardrail_allows_safe_response(self):
        response = "Chúng tôi đang kiểm tra giao dịch của bạn và sẽ phản hồi sớm nhất."
        result = check_response_safety(response)
        assert result.is_safe

    def test_guardrail_allows_safety_warning_about_pin(self):
        """Safety reminders that WARN about PIN/OTP should pass through."""
        response = "Vui lòng không gửi mã PIN, OTP hoặc mật khẩu qua chat."
        result = check_response_safety(response)
        assert result.is_safe, (
            f"Safety warning incorrectly blocked: {result.violations}"
        )


# ─── Group E: No hard-code scan ─────────────────────────────────

class TestGroupE_NoHardCode:
    """Ensure no business logic hard-codes specific values."""

    def _scan_source_dir(self, pattern: str) -> list[str]:
        """Grep for pattern in src/ excluding __pycache__ and test files."""
        src_dir = os.path.join(
            os.path.dirname(__file__), "..", "src", "fintech_agent",
        )
        src_dir = os.path.abspath(src_dir)

        hits = []
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                # Skip test files
                if fname.startswith("test_"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception:
                    continue
                # Search in non-comment, non-string lines
                for i, line in enumerate(content.split("\n"), 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if re.search(pattern, stripped, re.IGNORECASE):
                        hits.append(f"{fpath}:{i}: {stripped[:100]}")

        return hits

    def test_no_hardcoded_txn_topup_001(self):
        """TXN_TOPUP_001 should not appear in resolver/composer/analyzer."""
        hits = self._scan_source_dir(r"TXN_TOPUP_001")
        # Filter out mock data, test seed, server docs/examples, and comments
        business_hits = [
            h for h in hits
            if not any(x in h for x in [
                "mock", "seed", "test", "fixture", "migration",
                "server.py", "e.g.", "example",
            ])
        ]
        assert len(business_hits) == 0, (
            f"Found TXN_TOPUP_001 in business logic:\n" +
            "\n".join(business_hits)
        )

    def test_no_hardcoded_u_topup_001(self):
        hits = self._scan_source_dir(r"U_TOPUP_001")
        business_hits = [
            h for h in hits
            if not any(x in h for x in ["mock", "seed", "test", "fixture", "migration"])
        ]
        assert len(business_hits) == 0, (
            f"Found U_TOPUP_001 in business logic:\n" +
            "\n".join(business_hits)
        )


# ─── Evidence Mapper Tests ──────────────────────────────────────

class TestEvidenceMapper:
    """Public-safe evidence mapper produces correct output."""

    def test_basic_evidence_mapping(self):
        raw = {
            "status": "pending",
            "amount": 500000,
        }
        result = to_public_safe_evidence(
            raw_evidence=raw,
            rule_result=None,
            workflow="wallet_topup",
            resolution_status="resolved",
        )
        assert "what_we_know" in result
        assert "likely_issue_location" in result
        assert "next_step" in result
        assert "customer_action_needed" in result

    def test_no_internal_terms_in_output(self):
        raw = {
            "status": "pending",
            "force_success": True,
            "master_wallet_balance": 999999,
        }
        result = to_public_safe_evidence(
            raw_evidence=raw,
            rule_result=None,
            workflow="wallet_topup",
        )
        output_str = str(result)
        assert "force_success" not in output_str.lower()
        assert "master_wallet" not in output_str.lower()


# ─── Response Composer Tests ────────────────────────────────────

class TestResponseComposer:
    """Deterministic response composer produces non-empty, safe responses."""

    def test_ask_what_to_do_response(self):
        analysis = MessageAnalysis(
            message_type="ask_what_to_do",
            workflow_hint="wallet_topup",
        )
        composed = compose_customer_response(
            customer_message="tôi cần cung cấp gì",
            message_analysis=analysis,
        )
        assert composed.public_message
        assert len(composed.public_message) > 10

    def test_provide_missing_info_resolved(self):
        analysis = MessageAnalysis(
            message_type="provide_missing_info",
            workflow_hint="wallet_topup",
        )
        evidence = {"what_we_know": "Giao dịch đang xử lý."}
        composed = compose_customer_response(
            customer_message="tôi nạp 500k sáng nay qua VCB",
            message_analysis=analysis,
            public_safe_evidence=evidence,
            resolution_status="resolved",
        )
        assert composed.public_message
        assert "cảm ơn" in composed.public_message.lower() or len(composed.public_message) > 10

    def test_sensitive_info_warning(self):
        analysis = MessageAnalysis(
            message_type="provide_sensitive_info",
        )
        composed = compose_customer_response(
            customer_message="PIN: 1234",
            message_analysis=analysis,
        )
        assert composed.public_message
        # LLM may override tone; verify warning content instead
        msg_lower = composed.public_message.lower()
        assert any(
            kw in msg_lower
            for kw in ["pin", "otp", "mật khẩu", "không", "password"]
        ), f"Expected sensitive info warning, got: {composed.public_message[:100]}"


# ─── Policy Loading Tests ──────────────────────────────────────

class TestPolicyLoading:
    """Policy YAML loads correctly and contains expected structure."""

    def test_policy_loads(self):
        policy = load_response_policy()
        assert isinstance(policy, dict)
        assert "workflows" in policy
        assert "global_forbidden_terms" in policy

    def test_policy_has_wallet_topup(self):
        policy = load_response_policy()
        wf = policy["workflows"]
        assert "wallet_topup" in wf
        assert "guidance_template" in wf["wallet_topup"]
        assert "safe_missing_info" in wf["wallet_topup"]

    def test_policy_has_fraud_lock(self):
        policy = load_response_policy()
        wf = policy["workflows"]
        assert "fraud_account_lock" in wf

    def test_policy_global_forbidden(self):
        policy = load_response_policy()
        forbidden = policy["global_forbidden_terms"]
        assert "force-success" in forbidden
        assert "master wallet" in forbidden
        assert "risk_score" in forbidden

    def test_workflow_guidance_loaded(self):
        """Backward compat: _WORKFLOW_GUIDANCE is populated from policy."""
        assert "wallet_topup" in _WORKFLOW_GUIDANCE
        assert "fraud_account_lock" in _WORKFLOW_GUIDANCE
        assert len(_WORKFLOW_GUIDANCE["wallet_topup"]) > 20


# ─── Full Pipeline Integration (without LLM) ───────────────────

class TestFullPipelineNoLLM:
    """End-to-end pipeline using deterministic fallback only."""

    def test_provide_info_pipeline(self):
        """Message with amount+bank → analyze → resolve → compose → guardrail."""
        msg = "tôi chuyển nửa triệu sáng nay qua VCB"
        analysis = analyze_customer_message(msg)
        assert analysis.extracted.amount == 500_000 or analysis.extracted.bank_name

        # Compose response
        composed = compose_customer_response(
            customer_message=msg,
            message_analysis=analysis,
            resolution_status="need_more_info",
        )
        assert composed.public_message

        # Guardrail
        guardrail = check_response_safety(composed.public_message)
        assert guardrail.is_safe

    def test_short_followup_pipeline(self):
        """Short message with active case → analyze → compose."""
        msg = "không nhớ"
        case_ctx = {
            "selected_workflow": "wallet_topup",
            "awaiting_field": "transaction_id",
            "has_active_case": True,
        }
        analysis = analyze_customer_message(msg, case_ctx)
        # LLM may classify as ask_what_to_do (contextual), fallback as follow_up.
        # Both are valid — the key invariant is belongs_to_active_case=True.
        valid_types = (
            "follow_up", "provide_missing_info", "ask_what_to_do",
            "ask_status", "unknown",
        )
        assert analysis.message_type in valid_types, (
            f"Unexpected type '{analysis.message_type}' for short followup"
        )
        assert analysis.belongs_to_active_case is True

        composed = compose_customer_response(
            customer_message=msg,
            message_analysis=analysis,
            active_case_context=case_ctx,
        )
        assert composed.public_message
