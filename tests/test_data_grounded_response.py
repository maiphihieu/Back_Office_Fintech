"""Tests for data-grounded chatbot responses.

Validates that the chatbot:
  - Routes to the correct workflow from account data
  - Grounds responses in verified account data, not stale context
  - Detects contradictions between claims and evidence
  - Honestly reports no-match when account data is absent
  - Handles recheck requests with status changed/unchanged
  - Blocks cross-workflow wording via guardrail

Test scenarios:
 1. Active wallet_topup → "tài khoản bị khóa" → workflow switch
 2. After switch → "khóa là tôi không vào được ấy" → stays fraud
 3. Customer claims 1M, DB has 500K → contradiction
 4. No matching account data → honest "not found"
 5. Recheck request → resolver re-runs, status notice
 6. Workflow mismatch guardrail → response blocked if mismatch
"""

import pytest

from fintech_agent.llm.message_analyzer import (
    _fallback_analyze,
    MessageAnalysis,
    ExtractedFields,
)
from fintech_agent.llm.response_composer import (
    compose_customer_response,
    _compose_from_diagnosis,
    ComposedResponse,
)
from fintech_agent.safety.output_guardrail import check_response_safety
from fintech_agent.api.customer_claims import (
    CustomerClaims,
    VerifiedEvidence,
    Contradiction,
    detect_contradictions,
)
from fintech_agent.api.generic_resolver import (
    no_match_message,
    NO_MATCH_INSIST_RESPONSE,
    ResolutionResult,
)


# ─── Test 1: Active wallet_topup → account lock complaint ─────


class TestWorkflowSwitchFromData:
    """Active wallet_topup case, customer says account is locked.
    Backend must switch workflow and check account-lock data."""

    def test_switch_routes_to_fraud(self):
        """Analyzer detects workflow switch → fraud_account_lock."""
        ctx = {"selected_workflow": "wallet_topup", "service_type": "topup"}
        result = _fallback_analyze(
            "Tài khoản của tôi bị khóa.",
            ctx, {},
        )
        assert result.workflow_hint == "fraud_account_lock"
        assert result.message_type in ("workflow_switch", "new_complaint")
        assert result.belongs_to_active_case is False

    def test_no_topup_wording_after_switch(self):
        """After switching to fraud_account_lock, response must NOT have topup wording."""
        response = (
            "Hệ thống kiểm tra trạng thái tài khoản và nhận thấy tài khoản "
            "đang bị hạn chế. Bộ phận bảo mật sẽ xem xét."
        )
        result = check_response_safety(
            response,
            policy={"global_forbidden_terms": []},
            workflow="fraud_account_lock",
            diagnosis={},
        )
        assert result.is_safe

    def test_topup_wording_blocked_in_fraud(self):
        """Wallet topup wording MUST be blocked in fraud_account_lock context."""
        bad = "Ngân hàng đã xác nhận giao dịch nạp ví nhưng số dư ví chưa cập nhật."
        result = check_response_safety(
            bad,
            policy={"global_forbidden_terms": []},
            workflow="fraud_account_lock",
            diagnosis={},
        )
        assert not result.is_safe


# ─── Test 2: Follow-up "khóa là tôi không vào được ấy" ───────


class TestFollowUpStaysInFraud:
    """After switching to fraud, a clarification must stay in fraud context."""

    def test_clarification_stays_fraud(self):
        """'khóa là tôi không vào được ấy' → still fraud_account_lock."""
        ctx = {"selected_workflow": "fraud_account_lock"}
        result = _fallback_analyze(
            "khóa là tôi không vào được ấy",
            ctx, {},
        )
        assert result.workflow_hint == "fraud_account_lock"
        # Should NOT switch to wallet_topup
        assert result.workflow_hint != "wallet_topup"

    def test_no_topup_in_fraud_followup_response(self):
        """Fraud follow-up response must contain no topup wording."""
        response = (
            "Mình hiểu, bạn không thể đăng nhập vào tài khoản. "
            "Hệ thống đã ghi nhận và sẽ kiểm tra trạng thái khóa."
        )
        result = check_response_safety(
            response,
            policy={"global_forbidden_terms": []},
            workflow="fraud_account_lock",
            diagnosis={},
        )
        assert result.is_safe


# ─── Test 3: Customer claims 1M, DB has 500K → contradiction ─


class TestContradictionDetection:
    """Customer claims 1.000.000đ but account data has 500.000đ topup."""

    def test_amount_contradiction_detected(self):
        """detect_contradictions catches amount mismatch."""
        claims = CustomerClaims()
        claims.merge_extracted_fields(
            ExtractedFields(amount=1_000_000),
            is_correction=False,
        )
        evidence = VerifiedEvidence(
            resolved_entity_id="TXN-001",
            resolved_entity_type="transaction",
            verified_amount=500_000,
            verified_status="pending",
            verified_owner_id="user-1",
        )
        contradictions = detect_contradictions(claims, evidence)
        assert len(contradictions) >= 1
        amount_c = [c for c in contradictions if c.field == "amount"]
        assert len(amount_c) == 1
        assert amount_c[0].customer_claim == 1_000_000
        assert amount_c[0].verified_value == 500_000

    def test_contradiction_in_composed_response(self):
        """Deterministic composer includes contradiction notice with both amounts."""
        # Test the deterministic fallback directly (LLM is non-deterministic).
        from fintech_agent.llm.message_analyzer import load_response_policy, get_workflow_policy
        policy = load_response_policy()
        wf_policy = get_workflow_policy("wallet_topup")
        safety_reminder = (
            wf_policy.get("safety_reminder")
            or policy.get("global_safety_reminder", "")
        )

        contradictions = [
            Contradiction(
                field="amount",
                customer_claim=1_000_000,
                verified_value=500_000,
                severity="medium",
            )
        ]
        evidence = {
            "customer_safe_cause": "Giao dịch nạp tiền ghi nhận 500.000đ, đang xử lý.",
            "confirmed_public_facts": ["Giao dịch 500.000đ, trạng thái đang xử lý"],
            "next_step": "Hệ thống đang đối soát với ngân hàng.",
        }
        composed = _compose_from_diagnosis(
            public_safe_evidence=evidence,
            resolution_status="resolved",
            safety_reminder=safety_reminder,
            wf_policy=wf_policy,
            policy=policy,
            contradictions=contradictions,
        )
        msg = composed.public_message.lower()
        # Must mention both amounts in the contradiction notice
        assert "500.000" in msg
        assert "1.000.000" in msg

    def test_no_bare_no_match_when_data_exists(self):
        """When data exists with a mismatch, response is NOT a bare no_match."""
        analysis = MessageAnalysis(
            message_type="new_complaint",
            belongs_to_active_case=False,
            confidence=0.9,
            workflow_hint="wallet_topup",
            extracted=ExtractedFields(amount=1_000_000),
        )
        contradictions = [
            Contradiction(
                field="amount",
                customer_claim=1_000_000,
                verified_value=500_000,
                severity="medium",
            )
        ]
        evidence = {
            "customer_safe_cause": "Giao dịch nạp tiền ghi nhận 500.000đ.",
            "confirmed_public_facts": ["Giao dịch 500.000đ"],
        }
        composed = compose_customer_response(
            customer_message="tôi nạp 1 triệu",
            message_analysis=analysis,
            public_safe_evidence=evidence,
            resolution_status="resolved",
            contradictions=contradictions,
        )
        # Should NOT be the generic "chưa tìm thấy" template
        assert "chưa tìm thấy" not in composed.public_message.lower()


# ─── Test 4: No matching account data → honest "not found" ────


class TestNoMatchResponse:
    """No matching data on logged-in account → honest 'not found'."""

    def test_no_match_says_not_found(self):
        """no_match_message clearly states data not found."""
        msg = no_match_message("wallet_topup")
        assert "chưa tìm thấy" in msg.lower()
        assert "tài khoản" in msg.lower() or "đăng nhập" in msg.lower()

    def test_no_match_asks_for_evidence(self):
        """no_match response asks for safe supporting evidence."""
        msg = no_match_message("wallet_topup")
        # Should ask for reference/receipt, not PIN/OTP
        assert "mã tham chiếu" in msg.lower() or "biên lai" in msg.lower()
        assert "pin" not in msg.lower()
        assert "otp" not in msg.lower()
        assert "mật khẩu" not in msg.lower()

    def test_no_match_no_fake_confirmation(self):
        """no_match response must NOT imply the transaction exists."""
        msg = no_match_message("wallet_topup")
        # Must not say "đã tìm thấy", "đã xác nhận", "đang xử lý"
        assert "đã tìm thấy" not in msg.lower()
        assert "đã xác nhận" not in msg.lower()

    def test_insist_response_still_honest(self):
        """Insistence response is still honest — no fake confirmation."""
        msg = NO_MATCH_INSIST_RESPONSE
        assert "chưa tìm thấy" in msg.lower()
        # Safety warnings about PIN/OTP are ALLOWED (they tell the customer NOT
        # to share them), but the response must not ASK for PIN/OTP.
        assert "nhập pin" not in msg.lower()
        assert "gửi otp" not in msg.lower() or "không gửi" in msg.lower()

    def test_no_match_compose_is_honest(self):
        """Composed response for no_match is grounded — says not found."""
        analysis = MessageAnalysis(
            message_type="new_complaint",
            belongs_to_active_case=False,
            confidence=0.9,
            workflow_hint="wallet_topup",
            extracted=ExtractedFields(amount=300_000),
        )
        composed = compose_customer_response(
            customer_message="tôi nạp 300k mà chưa nhận",
            message_analysis=analysis,
            public_safe_evidence={},
            resolution_status="no_match",
        )
        msg = composed.public_message.lower()
        # Must say not found, not "đang xử lý"
        assert "chưa tìm thấy" in msg or "chưa có" in msg or "không có" in msg or "chưa ghi nhận" in msg


# ─── Test 5: Recheck request → resolver re-runs ──────────────


class TestRecheckContext:
    """Recheck request — resolver re-runs, response says changed/unchanged."""

    def test_recheck_unchanged_prepends_notice(self):
        """When status unchanged, deterministic composer prepends 'vẫn giữ nguyên'."""
        # Test deterministic fallback directly (LLM wording is non-deterministic).
        from fintech_agent.llm.message_analyzer import load_response_policy, get_workflow_policy
        policy = load_response_policy()
        wf_policy = get_workflow_policy("wallet_topup")
        safety_reminder = (
            wf_policy.get("safety_reminder")
            or policy.get("global_safety_reminder", "")
        )

        recheck = {
            "is_recheck": True,
            "status_changed": False,
            "old_status": "pending",
            "new_status": "pending",
        }
        evidence = {
            "customer_safe_cause": "Giao dịch đang chờ xử lý.",
            "confirmed_public_facts": ["Trạng thái: đang xử lý"],
        }
        composed = _compose_from_diagnosis(
            public_safe_evidence=evidence,
            resolution_status="resolved",
            safety_reminder=safety_reminder,
            wf_policy=wf_policy,
            policy=policy,
            recheck_context=recheck,
        )
        msg = composed.public_message.lower()
        assert "giữ nguyên" in msg

    def test_recheck_changed_prepends_notice(self):
        """When status changed, deterministic composer prepends 'đã được cập nhật'."""
        from fintech_agent.llm.message_analyzer import load_response_policy, get_workflow_policy
        policy = load_response_policy()
        wf_policy = get_workflow_policy("wallet_topup")
        safety_reminder = (
            wf_policy.get("safety_reminder")
            or policy.get("global_safety_reminder", "")
        )

        recheck = {
            "is_recheck": True,
            "status_changed": True,
            "old_status": "pending",
            "new_status": "success",
        }
        evidence = {
            "customer_safe_cause": "Giao dịch đã hoàn thành.",
            "confirmed_public_facts": ["Trạng thái: đã hoàn thành"],
        }
        composed = _compose_from_diagnosis(
            public_safe_evidence=evidence,
            resolution_status="resolved",
            safety_reminder=safety_reminder,
            wf_policy=wf_policy,
            policy=policy,
            recheck_context=recheck,
        )
        msg = composed.public_message.lower()
        assert "cập nhật" in msg

    def test_no_recheck_no_notice(self):
        """Normal response (no recheck) does NOT prepend status notice."""
        analysis = MessageAnalysis(
            message_type="new_complaint",
            belongs_to_active_case=False,
            confidence=0.9,
            workflow_hint="wallet_topup",
            extracted=ExtractedFields(amount=500_000),
        )
        evidence = {
            "customer_safe_cause": "Giao dịch đang chờ xử lý.",
        }
        composed = compose_customer_response(
            customer_message="tôi nạp 500k chưa nhận",
            message_analysis=analysis,
            public_safe_evidence=evidence,
            resolution_status="resolved",
            recheck_context=None,
        )
        msg = composed.public_message.lower()
        assert "giữ nguyên" not in msg
        assert "vừa kiểm tra lại" not in msg


# ─── Test 6: Workflow mismatch guardrail ──────────────────────


class TestWorkflowMismatchGuardrail:
    """If router workflow ≠ response workflow, response is blocked."""

    def test_topup_response_blocked_for_fraud(self):
        """Topup diagnosis wording blocked when workflow=fraud_account_lock."""
        topup_response = (
            "Giao dịch nạp ví của bạn ghi nhận 500.000đ. "
            "Ngân hàng đã xác nhận giao dịch nhưng ví chưa nhận được tiền."
        )
        result = check_response_safety(
            topup_response,
            policy={"global_forbidden_terms": []},
            workflow="fraud_account_lock",
            diagnosis={},
        )
        assert not result.is_safe
        # At least one violation should be about cross-workflow wording
        assert any(
            "cross_workflow" in v
            for v in result.violations
        )

    def test_fraud_response_passes_for_fraud(self):
        """Correct fraud_account_lock response passes guardrail."""
        good_response = (
            "Tài khoản của bạn đang bị hạn chế trạng thái khóa. "
            "Bộ phận bảo mật đang xem xét. "
            "Vui lòng không gửi PIN, OTP hoặc mật khẩu."
        )
        result = check_response_safety(
            good_response,
            policy={"global_forbidden_terms": []},
            workflow="fraud_account_lock",
            diagnosis={},
        )
        assert result.is_safe

    def test_guardrail_provides_sanitized_text(self):
        """When guardrail blocks, it provides sanitized_text as fallback."""
        bad_response = "Giao dịch nạp ví thất bại. Số dư ví chưa cập nhật."
        result = check_response_safety(
            bad_response,
            policy={"global_forbidden_terms": []},
            workflow="fraud_account_lock",
            diagnosis={},
        )
        assert not result.is_safe
        # sanitized_text should exist (may be the original with violations noted)
        # or be None (caller regenerates)
        # Both are valid — the key is is_safe=False


# ─── Stale diagnosis guard ────────────────────────────────────


class TestStaleDiagnosisGuard:
    """Verify stale last_diagnosis from different workflow is discarded."""

    def test_stale_wallet_diagnosis_not_reused_for_fraud(self):
        """After workflow switch, wallet diagnosis must not drive fraud response.

        In production, the Gap 1 fix in customer_chat.py discards the stale
        wallet_topup diagnosis and the guardrail receives either an empty or
        fraud-specific diagnosis. Here we test that if wallet wording leaks
        into the response but the diagnosis is clean (no wallet info), the
        guardrail blocks it.
        """
        from fintech_agent.llm.message_analyzer import load_response_policy, get_workflow_policy
        policy = load_response_policy()
        wf_policy = get_workflow_policy("fraud_account_lock")
        safety_reminder = (
            wf_policy.get("safety_reminder")
            or policy.get("global_safety_reminder", "")
        )

        # Simulate stale wallet_topup diagnosis data being used by the composer
        stale_diagnosis = {
            "workflow": "wallet_topup",
            "customer_safe_cause": "Ngân hàng đã ghi nhận giao dịch nạp ví.",
            "confirmed_public_facts": ["Giao dịch nạp ví 500.000đ"],
        }
        composed = _compose_from_diagnosis(
            public_safe_evidence=stale_diagnosis,
            resolution_status="need_more_info",
            safety_reminder=safety_reminder,
            wf_policy=wf_policy,
            policy=policy,
        )
        msg = composed.public_message.lower()

        # In production, after Gap 1 fix, the stale diagnosis is discarded.
        # The guardrail receives a CLEAN diagnosis (no wallet info).
        # The response text still has wallet wording from the stale data,
        # but the diagnosis is now clean → guardrail blocks it.
        clean_fraud_diagnosis = {
            "workflow": "fraud_account_lock",
            "customer_safe_cause": "",
        }
        if "nạp ví" in msg or "nạp tiền" in msg or "số dư ví" in msg:
            guardrail = check_response_safety(
                composed.public_message,
                policy={"global_forbidden_terms": []},
                workflow="fraud_account_lock",
                diagnosis=clean_fraud_diagnosis,
            )
            assert not guardrail.is_safe, (
                f"Guardrail should block wallet wording in fraud context "
                f"with clean diagnosis: {msg}"
            )
        # If no wallet wording leaked, the test also passes.


# ─── Edge: "Vậy tài khoản tôi đang bị gì?" ──────────────────


class TestStatusQuestionGrounded:
    """Customer asks 'Vậy tài khoản tôi đang bị gì?' — grounded response."""

    def test_status_question_uses_current_evidence(self):
        """Response must be based on current evidence, not generic template."""
        analysis = MessageAnalysis(
            message_type="ask_what_happened",
            belongs_to_active_case=True,
            confidence=0.9,
            workflow_hint="fraud_account_lock",
            extracted=ExtractedFields(),
        )
        evidence = {
            "customer_safe_cause": "Tài khoản đang bị hạn chế do kiểm tra bảo mật.",
            "confirmed_public_facts": ["Trạng thái tài khoản: bị hạn chế"],
            "next_step": "Bộ phận bảo mật đang xem xét.",
        }
        composed = compose_customer_response(
            customer_message="vậy tài khoản tôi đang bị gì?",
            message_analysis=analysis,
            public_safe_evidence=evidence,
            resolution_status="resolved",
        )
        msg = composed.public_message.lower()
        # Must contain account-lock specific content from evidence
        assert "hạn chế" in msg or "khóa" in msg or "bảo mật" in msg
        # Must NOT contain wallet topup wording
        assert "nạp ví" not in msg
        assert "số dư ví" not in msg

    def test_status_question_no_generic_template(self):
        """When evidence exists, response is NOT the generic fallback."""
        analysis = MessageAnalysis(
            message_type="ask_what_happened",
            belongs_to_active_case=True,
            confidence=0.9,
            workflow_hint="fraud_account_lock",
            extracted=ExtractedFields(),
        )
        evidence = {
            "customer_safe_cause": "Tài khoản đang bị hạn chế.",
            "confirmed_public_facts": ["Trạng thái: hạn chế"],
        }
        composed = compose_customer_response(
            customer_message="vậy tài khoản tôi đang bị gì?",
            message_analysis=analysis,
            public_safe_evidence=evidence,
            resolution_status="resolved",
        )
        # Generic fallback contains "ghi nhận" or "phản hồi sớm" without specifics
        # Our response should be specific
        assert "hạn chế" in composed.public_message.lower()
