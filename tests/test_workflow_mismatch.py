"""Tests for workflow mismatch guardrail and account-lock routing.

Verifies:
 1. Active wallet_topup + "tài khoản bị khóa" → workflow_switch to fraud_account_lock
 2. Fresh chat + "không rút tiền được vì bị khóa" → fraud_account_lock
 3. Active fraud_account_lock + "khi nào mở lại?" → same fraud case
 4. Active wallet_topup + "khi nào nhận tiền?" → same wallet_topup case
 5. Response guardrail blocks topup wording in fraud_account_lock context
 6. No risk_score/fraud_status leak in any response
"""

import pytest

from fintech_agent.llm.message_analyzer import (
    _fallback_analyze,
    _detect_workflow_hint_from_message,
    _ACCOUNT_LOCK_RE,
    MessageAnalysis,
)
from fintech_agent.safety.output_guardrail import (
    check_response_safety,
)
from fintech_agent.api.customer_chat import _is_workflow_switch
from fintech_agent.llm.message_analyzer import ActiveCaseContext


# ─── Account-lock signal detection ────────────────────────────


class TestAccountLockDetection:
    """Verifies account-lock regex catches relevant Vietnamese phrases."""

    ACCOUNT_LOCK_PHRASES = [
        "Tài khoản của tôi bất ngờ bị khóa vô cớ.",
        "Tôi không rút tiền được vì tài khoản bị khóa.",
        "tài khoản bị block",
        "tài khoản đang bị hạn chế",
        "tại sao khóa tài khoản tôi",
        "account locked",
        "tôi không đăng nhập được vì bị khóa",
        "tài khoản bị đóng băng",
        "khóa vô cớ",
        "khóa bất ngờ",
        "không thể giao dịch được vì bị chặn",
        "bảo mật đã khóa tài khoản",
        "tk bị khóa",
    ]

    @pytest.mark.parametrize("phrase", ACCOUNT_LOCK_PHRASES)
    def test_regex_matches(self, phrase):
        assert _ACCOUNT_LOCK_RE.search(phrase), f"Regex did not match: '{phrase}'"

    @pytest.mark.parametrize("phrase", ACCOUNT_LOCK_PHRASES)
    def test_workflow_hint_detection(self, phrase):
        hint = _detect_workflow_hint_from_message(phrase)
        assert hint == "fraud_account_lock", f"'{phrase}' detected as '{hint}'"

    NOT_ACCOUNT_LOCK = [
        "tôi nạp tiền 500k mà ví chưa nhận",
        "giao dịch topup bị lỗi",
        "ngân hàng trừ tiền rồi mà chưa vào ví",
        "tàu khóa cửa",   # "khóa" but not account-lock context
        "ok",
        "xin chào",
    ]

    @pytest.mark.parametrize("phrase", NOT_ACCOUNT_LOCK)
    def test_no_false_positive(self, phrase):
        hint = _detect_workflow_hint_from_message(phrase)
        assert hint != "fraud_account_lock", f"False positive: '{phrase}'"


# ─── Test 1: Active wallet_topup → account lock complaint ─────


class TestWorkflowSwitchAccountLock:
    """Active wallet_topup case, customer says account is locked."""

    def test_analyzer_detects_workflow_switch(self):
        """Analyzer should set workflow_hint=fraud_account_lock, not wallet_topup."""
        ctx = {"selected_workflow": "wallet_topup", "service_type": "topup"}
        result = _fallback_analyze(
            "Tài khoản của tôi bất ngờ bị khóa vô cớ.",
            ctx, {},
        )
        assert result.workflow_hint == "fraud_account_lock"
        assert result.message_type in ("workflow_switch", "new_complaint")
        assert result.belongs_to_active_case is False

    def test_no_wallet_topup_wording_in_response(self):
        """Response for fraud_account_lock must NOT contain wallet topup wording."""
        bad_response = (
            "Hệ thống đã kiểm tra giao dịch nạp ví của bạn. "
            "Ngân hàng đã xác nhận giao dịch nhưng số dư ví chưa cập nhật."
        )
        result = check_response_safety(
            bad_response,
            policy={"global_forbidden_terms": []},
            workflow="fraud_account_lock",
            diagnosis={},
        )
        assert not result.is_safe, (
            "Wallet topup wording should be blocked in fraud_account_lock context"
        )

    def test_no_risk_score_leak(self):
        """risk_score should always be blocked."""
        bad_response = "Tài khoản có risk_score = 85, khả năng gian lận cao."
        result = check_response_safety(bad_response, policy={}, workflow="fraud_account_lock")
        assert not result.is_safe
        assert result.violations  # At least one violation

    def test_no_fraud_status_leak(self):
        """fraud_status should always be blocked."""
        bad_response = "fraud_status hiện tại là 'suspected'."
        result = check_response_safety(bad_response, policy={}, workflow="fraud_account_lock")
        assert not result.is_safe
        assert result.violations  # At least one violation


# ─── Test 2: Fresh chat → account lock complaint ──────────────


class TestFreshChatAccountLock:
    """Fresh chat, customer says cannot withdraw because account is locked."""

    def test_fresh_chat_routes_to_fraud(self):
        """No active case → should detect fraud_account_lock from message."""
        ctx = {}  # No active case
        result = _fallback_analyze(
            "Tôi không rút tiền được vì tài khoản bị khóa.",
            ctx, {},
        )
        assert result.workflow_hint == "fraud_account_lock"
        assert result.message_type in ("new_complaint", "workflow_switch", "follow_up")

    def test_safe_response_for_account_lock(self):
        """A proper account-lock response should pass guardrail."""
        good_response = (
            "Mình đã ghi nhận vấn đề tài khoản bị hạn chế. "
            "Hệ thống sẽ kiểm tra trạng thái khóa tài khoản và lý do hạn chế thao tác. "
            "Nếu cần, yêu cầu sẽ được chuyển bộ phận phụ trách kiểm tra bảo mật. "
            "Vui lòng không gửi PIN, OTP hoặc mật khẩu."
        )
        result = check_response_safety(
            good_response,
            policy={"global_forbidden_terms": []},
            workflow="fraud_account_lock",
            diagnosis={},
        )
        assert result.is_safe, f"Safe response was blocked: {result.violations}"


# ─── Test 3: Active fraud case → follow-up "khi nào mở lại?" ──


class TestFraudCaseFollowUp:
    """Active fraud_account_lock case, customer asks 'khi nào mở lại?'"""

    def test_follow_up_stays_in_fraud_case(self):
        """'Khi nào mở lại được?' should stay in fraud_account_lock case."""
        ctx = {"selected_workflow": "fraud_account_lock"}
        result = _fallback_analyze("Khi nào mở lại được?", ctx, {})
        # Should NOT be a workflow switch — no different workflow signal
        assert result.workflow_hint == "fraud_account_lock"
        assert result.message_type not in ("workflow_switch",)

    def test_no_unlock_promise(self):
        """Response for fraud case should never promise unlock."""
        # "mở khóa ngay" type promises should be avoided
        # The guardrail doesn't specifically block this, but let's verify
        # a good response passes
        good_response = (
            "Yêu cầu của bạn đã được ghi nhận. "
            "Bộ phận bảo mật sẽ kiểm tra và phản hồi trong thời gian sớm nhất. "
            "Vui lòng không gửi PIN, OTP hoặc mật khẩu."
        )
        result = check_response_safety(
            good_response,
            policy={"global_forbidden_terms": []},
            workflow="fraud_account_lock",
            diagnosis={},
        )
        assert result.is_safe


# ─── Test 4: Active wallet_topup → "khi nào nhận tiền?" ───────


class TestWalletTopupFollowUp:
    """Active wallet_topup case, customer asks 'khi nào nhận tiền?'"""

    def test_follow_up_stays_in_wallet_case(self):
        """'Khi nào tôi nhận được tiền?' should stay in wallet_topup case."""
        ctx = {"selected_workflow": "wallet_topup", "service_type": "topup"}
        result = _fallback_analyze("Khi nào tôi nhận được tiền?", ctx, {})
        assert result.workflow_hint == "wallet_topup"
        # Should NOT be classified as fraud_account_lock
        assert result.workflow_hint != "fraud_account_lock"
        # Should be a follow-up, not a new complaint
        assert result.message_type not in ("workflow_switch",)


# ─── Cross-workflow guardrail patterns ─────────────────────────


class TestCrossWorkflowGuardrail:
    """Verify topup wording is blocked in non-wallet workflows."""

    TOPUP_WORDING = [
        "ngân hàng đã xác nhận giao dịch",
        "giao dịch nạp ví đã hoàn tất",
        "ví chưa nhận được tiền",
        "nạp tiền vào ví",
        "kiểm tra giao dịch nạp tiền",
        "giao dịch topup",
    ]

    @pytest.mark.parametrize("wording", TOPUP_WORDING)
    def test_blocked_in_fraud_account_lock(self, wording):
        """Topup wording is blocked in fraud_account_lock workflow."""
        result = check_response_safety(
            f"Hệ thống kiểm tra: {wording}.",
            policy={"global_forbidden_terms": []},
            workflow="fraud_account_lock",
            diagnosis={},
        )
        assert not result.is_safe, (
            f"Topup wording '{wording}' should be blocked in fraud_account_lock"
        )

    @pytest.mark.parametrize("wording", TOPUP_WORDING)
    def test_allowed_in_wallet_topup(self, wording):
        """Topup wording is allowed in wallet_topup workflow."""
        result = check_response_safety(
            f"Hệ thống kiểm tra: {wording}.",
            policy={"global_forbidden_terms": []},
            workflow="wallet_topup",
            diagnosis={},
        )
        # Should NOT be blocked by topup-wording check
        topup_violations = [
            v for v in result.violations
            if "cross_workflow_topup_wording" in v
        ]
        assert not topup_violations, (
            f"Topup wording '{wording}' should be allowed in wallet_topup"
        )

    @pytest.mark.parametrize("wording", TOPUP_WORDING)
    def test_blocked_in_train_ticket(self, wording):
        """Topup wording is blocked in train_ticket workflow."""
        result = check_response_safety(
            f"Hệ thống kiểm tra: {wording}.",
            policy={"global_forbidden_terms": []},
            workflow="train_ticket",
            diagnosis={},
        )
        assert not result.is_safe, (
            f"Topup wording '{wording}' should be blocked in train_ticket"
        )


# ─── Edge cases ───────────────────────────────────────────────


class TestWorkflowMismatchEdgeCases:
    """Edge cases for the workflow mismatch guardrail."""

    def test_thank_you_no_switch(self):
        """'cảm ơn' with active wallet_topup should NOT switch to fraud."""
        ctx = {"selected_workflow": "wallet_topup"}
        result = _fallback_analyze("cảm ơn", ctx, {})
        assert result.workflow_hint != "fraud_account_lock"

    def test_pure_amount_no_switch(self):
        """'500k' with active wallet_topup should NOT switch to fraud."""
        ctx = {"selected_workflow": "wallet_topup"}
        result = _fallback_analyze("500k", ctx, {})
        assert result.workflow_hint != "fraud_account_lock"

    def test_same_workflow_signal_no_switch(self):
        """Account lock msg with active fraud case → same case, no switch."""
        ctx = {"selected_workflow": "fraud_account_lock"}
        result = _fallback_analyze("tài khoản vẫn bị khóa", ctx, {})
        # Should stay in the same workflow, not switch
        assert result.workflow_hint == "fraud_account_lock"
        assert result.message_type not in ("workflow_switch",)
