"""Tests for workflow routing decoupling from profile/demo data.

Validates:
 1. Train demo account + wallet_topup complaint → wallet_topup routing
 2. Train demo account + train_ticket complaint → train_ticket routing
 3. Wallet demo account + train_ticket complaint → train_ticket routing
 4. Active train case + "còn vụ nạp ví" → workflow switch to wallet_topup
 5. Guardrail: wallet_topup response with train wording → blocked
"""

import pytest
import re

from fintech_agent.llm.message_analyzer import (
    _fallback_analyze,
    MessageAnalysis,
    ExtractedFields,
)
from fintech_agent.llm.mock_extractor import mock_extract
from fintech_agent.safety.output_guardrail import check_response_safety


# ─── Test 1: Train demo → wallet_topup complaint ─────────────


class TestTrainDemoWalletComplaint:
    """Logged in as train-ticket demo, customer says wallet_topup complaint."""

    def test_analyzer_routes_wallet_topup(self):
        """'tôi nạp tiền vào tài khoản nhưng ví không nhận' → wallet_topup."""
        msg = "tôi nạp tiền vào tài khoản nhưng ví không nhận"
        # No active case context — fresh complaint
        result = _fallback_analyze(msg, {}, {})
        assert result.workflow_hint == "wallet_topup"

    def test_analyzer_routes_wallet_even_with_train_context(self):
        """Even with active train_ticket case, wallet complaint routes to wallet."""
        msg = "tôi nạp tiền vào tài khoản nhưng ví không nhận"
        # Active case is train_ticket
        ctx = {"selected_workflow": "train_ticket", "service_type": "train_ticket"}
        result = _fallback_analyze(msg, ctx, {})
        # Should detect workflow switch, not stay in train_ticket
        assert result.workflow_hint == "wallet_topup"
        assert result.belongs_to_active_case is False

    def test_mock_extractor_ignores_profile_name(self):
        """Mock extractor must not route by display_name in identity metadata."""
        # Simulates enriched complaint with train-ticket profile name
        enriched = (
            "tôi nạp tiền vào tài khoản nhưng ví không nhận "
            "[User ID: U_TRAIN_002] [Tên: Khách mua vé tàu demo 2]"
        )
        result = mock_extract(enriched, user_id="U_TRAIN_002")
        assert result.service_type == "wallet_topup"

    def test_mock_extractor_without_metadata_brackets(self):
        """Without metadata, extractor routes correctly."""
        plain = "tôi nạp tiền vào tài khoản nhưng ví không nhận"
        result = mock_extract(plain)
        assert result.service_type == "wallet_topup"

    def test_no_train_wording_in_wallet_response(self):
        """Wallet_topup response must NOT contain train-ticket wording."""
        bad_response = (
            "Vé chưa được phát hành do nhà cung cấp chưa xác nhận. "
            "Hệ thống đang đối soát vé."
        )
        result = check_response_safety(
            bad_response,
            policy={"global_forbidden_terms": []},
            workflow="wallet_topup",
            diagnosis={},
        )
        assert not result.is_safe
        assert any("cross_workflow_train" in v for v in result.violations)


# ─── Test 2: Train demo → train_ticket complaint ─────────────


class TestTrainDemoTrainComplaint:
    """Logged in as train demo, customer says train complaint → still train."""

    def test_train_complaint_routes_train(self):
        """'tôi thanh toán vé tàu rồi nhưng chưa nhận được vé' → train_ticket."""
        msg = "tôi thanh toán vé tàu rồi nhưng chưa nhận được vé"
        result = _fallback_analyze(msg, {}, {})
        assert result.workflow_hint == "train_ticket"

    def test_extractor_train_complaint(self):
        """Mock extractor routes train complaint correctly."""
        msg = "tôi thanh toán vé tàu rồi nhưng chưa nhận được vé"
        result = mock_extract(msg)
        assert result.service_type == "train_ticket"

    def test_train_response_passes_guardrail(self):
        """Correct train_ticket response passes guardrail."""
        good = (
            "Hệ thống kiểm tra và nhận thấy vé tàu chưa được phát hành. "
            "Nhà cung cấp đang xác nhận trạng thái."
        )
        result = check_response_safety(
            good,
            policy={"global_forbidden_terms": []},
            workflow="train_ticket",
            diagnosis={"workflow": "train_ticket"},
        )
        assert result.is_safe


# ─── Test 3: Wallet demo → train_ticket complaint ────────────


class TestWalletDemoTrainComplaint:
    """Logged in as wallet-topup demo, customer says train complaint."""

    def test_wallet_profile_train_complaint(self):
        """'tôi mua vé tàu rồi nhưng chưa nhận được vé' → train_ticket."""
        msg = "tôi mua vé tàu rồi nhưng chưa nhận được vé"
        result = _fallback_analyze(msg, {}, {})
        assert result.workflow_hint == "train_ticket"

    def test_extractor_with_wallet_profile_metadata(self):
        """Mock extractor with wallet profile metadata still routes train."""
        enriched = (
            "tôi mua vé tàu rồi nhưng chưa nhận được vé "
            "[User ID: U_TOPUP_001] [Wallet ID: WALLET_001]"
        )
        result = mock_extract(enriched, user_id="U_TOPUP_001")
        assert result.service_type == "train_ticket"

    def test_no_wallet_wording_in_train_response(self):
        """Train_ticket response must NOT contain wallet wording."""
        bad = "Giao dịch nạp ví của bạn đang chờ xử lý. Số dư ví chưa cập nhật."
        result = check_response_safety(
            bad,
            policy={"global_forbidden_terms": []},
            workflow="train_ticket",
            diagnosis={},
        )
        assert not result.is_safe


# ─── Test 4: Active train case → workflow switch to wallet ────


class TestWorkflowSwitchTrainToWallet:
    """Active train_ticket case, customer says 'còn vụ nạp ví của tôi thì sao'."""

    def test_switch_detected(self):
        """Workflow switch to wallet_topup detected."""
        msg = "còn vụ nạp ví của tôi thì sao"
        ctx = {"selected_workflow": "train_ticket"}
        result = _fallback_analyze(msg, ctx, {})
        assert result.workflow_hint == "wallet_topup"
        assert result.belongs_to_active_case is False

    def test_old_train_diagnosis_not_reused(self):
        """After switch, train diagnosis must not drive wallet response."""
        msg = "còn vụ nạp ví của tôi thì sao"
        ctx = {"selected_workflow": "train_ticket"}
        result = _fallback_analyze(msg, ctx, {})
        # The message type should indicate a switch or new complaint
        assert result.message_type in ("workflow_switch", "new_complaint")


# ─── Test 5: Response guardrail — cross-workflow blocking ─────


class TestCrossWorkflowGuardrail:
    """Guardrail blocks cross-workflow wording."""

    def test_train_wording_blocked_in_wallet(self):
        """Train-ticket wording blocked when workflow=wallet_topup."""
        bad = "Vé chưa được phát hành. Nhà cung cấp vé đang kiểm tra."
        result = check_response_safety(
            bad,
            policy={"global_forbidden_terms": []},
            workflow="wallet_topup",
            diagnosis={},
        )
        assert not result.is_safe
        assert any("cross_workflow_train" in v for v in result.violations)

    def test_wallet_wording_blocked_in_train(self):
        """Wallet wording blocked when workflow=train_ticket."""
        bad = "Giao dịch nạp ví đang chờ xử lý. Số dư ví chưa cập nhật."
        result = check_response_safety(
            bad,
            policy={"global_forbidden_terms": []},
            workflow="train_ticket",
            diagnosis={},
        )
        assert not result.is_safe

    def test_correct_wallet_passes(self):
        """Correct wallet_topup response passes in wallet context."""
        good = (
            "Theo kiểm tra dữ liệu hiện tại trên tài khoản đang đăng nhập, "
            "hệ thống chưa tìm thấy giao dịch nạp ví phù hợp."
        )
        result = check_response_safety(
            good,
            policy={"global_forbidden_terms": []},
            workflow="wallet_topup",
            diagnosis={"workflow": "wallet_topup"},
        )
        assert result.is_safe

    def test_correct_train_passes(self):
        """Correct train_ticket response passes in train context."""
        good = (
            "Hệ thống kiểm tra và nhận thấy vé chưa được phát hành. "
            "Nhà cung cấp vé đang xác nhận."
        )
        result = check_response_safety(
            good,
            policy={"global_forbidden_terms": []},
            workflow="train_ticket",
            diagnosis={"workflow": "train_ticket"},
        )
        assert result.is_safe

    def test_train_wording_blocked_in_fraud(self):
        """Train wording blocked in fraud_account_lock context."""
        bad = "Vé tàu của bạn chưa nhận được vé. Đối soát vé đang xử lý."
        result = check_response_safety(
            bad,
            policy={"global_forbidden_terms": []},
            workflow="fraud_account_lock",
            diagnosis={},
        )
        assert not result.is_safe

    def test_train_wording_allowed_with_train_diagnosis(self):
        """Train wording allowed when diagnosis says train-related."""
        good = "Vé tàu đang chờ nhà cung cấp vé xác nhận."
        result = check_response_safety(
            good,
            policy={"global_forbidden_terms": []},
            workflow="train_ticket",
            diagnosis={"workflow": "train_ticket"},
        )
        assert result.is_safe


# ─── Test: display_name stripping ─────────────────────────────


class TestDisplayNameStripping:
    """Verify identity metadata brackets are stripped for routing."""

    def test_strip_ten_label(self):
        """[Tên: ...] metadata is stripped for service_type keyword match."""
        enriched = (
            "tôi nạp tiền vào ví nhưng chưa nhận "
            "[Tên: Khách mua vé tàu demo 2]"
        )
        result = mock_extract(enriched)
        # Must NOT be train_ticket just because display_name contains "vé tàu"
        assert result.service_type == "wallet_topup"

    def test_strip_multiple_labels(self):
        """Multiple metadata labels stripped."""
        enriched = (
            "tài khoản bị khóa "
            "[User ID: U_TRAIN_002] [SĐT: 0912345678] "
            "[Tên: Khách mua vé tàu demo 2]"
        )
        result = mock_extract(enriched, user_id="U_TRAIN_002")
        assert result.service_type == "account_security"

    def test_no_stripping_of_real_complaint(self):
        """Real complaint text is preserved for routing."""
        plain = "tôi mua vé tàu nhưng chưa nhận"
        result = mock_extract(plain)
        assert result.service_type == "train_ticket"
