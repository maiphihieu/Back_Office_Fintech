"""Tests for customer chat acknowledgement behavior.

Verifies that short acknowledgement messages ("được rồi", "ok", "cảm ơn",
"tôi hiểu rồi") do NOT reset the conversation, show the welcome greeting,
create a new case, or trigger the full pipeline when an active case exists.
"""

import pytest
from unittest.mock import patch

from fintech_agent.llm.message_analyzer import (
    _fallback_classify,
    _fallback_analyze,
    ExtractedFields,
    NON_CASE_MESSAGE_TYPES,
)
from fintech_agent.llm.response_composer import compose_acknowledgement_response


# ─── Acknowledgement detection ──────────────────────────────────


class TestAcknowledgementDetection:
    """Verifies that short ack messages classify as 'thank_you'."""

    ACK_MESSAGES = [
        "được rồi",
        "ok",
        "cảm ơn",
        "tôi hiểu rồi",
        "cảm ơn bạn",
        "ok bạn",
        "thanks",
        "vâng ạ",
        "dạ",
        "dạ vâng",
        "biết rồi",
        "tôi hiểu",
        "mình hiểu rồi",
        "dc rồi",
        "ok nhé",
        "cảm ơn nhiều",
        "thôi được rồi",
    ]

    @pytest.mark.parametrize("msg", ACK_MESSAGES)
    def test_ack_classified_as_thank_you(self, msg):
        """All short ack messages should classify as 'thank_you'."""
        extracted = ExtractedFields()
        msg_type, confidence, is_correction = _fallback_classify(
            msg, extracted, has_active_case=True, awaiting_field="",
        )
        assert msg_type == "thank_you", (
            f"'{msg}' classified as '{msg_type}' instead of 'thank_you'"
        )
        assert confidence >= 0.8
        assert is_correction is False

    @pytest.mark.parametrize("msg", ACK_MESSAGES)
    def test_ack_also_classified_without_active_case(self, msg):
        """Ack messages should still classify as 'thank_you' even without active case."""
        extracted = ExtractedFields()
        msg_type, _, _ = _fallback_classify(
            msg, extracted, has_active_case=False, awaiting_field="",
        )
        assert msg_type == "thank_you"


# ─── Active case + acknowledgement → no reset ──────────────────


class TestActiveWalletTopupAck:
    """Test 1: Active wallet_topup case → customer says 'được rồi'."""

    def test_no_greeting_with_active_case(self):
        """The fallback analyzer should NOT return 'greeting' for ack + active case."""
        active_ctx = {
            "selected_workflow": "wallet_topup",
            "service_type": "topup",
            "awaiting_field": "",
        }
        result = _fallback_analyze("được rồi", active_ctx, {})
        assert result.message_type == "thank_you"
        # thank_you with active case → belongs_to_active_case irrelevant
        # The key point: NOT greeting, NOT new_complaint, NOT unknown.

    def test_no_new_complaint_for_short_ack(self):
        """Short ack should never be classified as a new complaint."""
        active_ctx = {"selected_workflow": "wallet_topup"}
        result = _fallback_analyze("ok", active_ctx, {})
        assert result.message_type != "new_complaint"
        assert result.message_type != "unknown"


class TestNoMatchCaseAck:
    """Test 2: Active no_match case → customer says 'ok'."""

    def test_ok_with_no_match_case(self):
        """'ok' with an active case should be 'thank_you', not re-trigger template."""
        active_ctx = {
            "selected_workflow": "wallet_topup",
            "awaiting_field": "bank_reference",
        }
        result = _fallback_analyze("ok", active_ctx, {})
        assert result.message_type == "thank_you"

    def test_no_match_ack_response_mentions_receipt(self):
        """Acknowledgement for no_match case should mention evidence submission."""
        reply = compose_acknowledgement_response(
            resolution_status="no_match",
            workflow="wallet_topup",
        )
        assert "cảm ơn" in reply.lower() or "ghi nhận" in reply.lower()
        assert any(kw in reply.lower() for kw in [
            "mã tham chiếu", "biên lai", "gửi tiếp",
        ])
        # Should NOT contain greeting text
        assert "xin chào" not in reply.lower()
        assert "trợ lý" not in reply.lower()


class TestCamOnAck:
    """Test 3: Active case → customer says 'cảm ơn'."""

    def test_cam_on_with_active_case(self):
        """'cảm ơn' should classify as 'thank_you'."""
        active_ctx = {"selected_workflow": "wallet_topup"}
        result = _fallback_analyze("cảm ơn", active_ctx, {})
        assert result.message_type == "thank_you"

    def test_resolved_ack_response(self):
        """Acknowledgement for resolved case is short and polite."""
        reply = compose_acknowledgement_response(
            resolution_status="resolved",
            workflow="wallet_topup",
        )
        assert "cảm ơn" in reply.lower() or "ghi nhận" in reply.lower()
        assert len(reply) < 300  # Short, not a full diagnosis
        # Must include safety reminder
        assert "pin" in reply.lower() or "otp" in reply.lower()


class TestNoActiveCaseGreeting:
    """Test 4: New session with no active case → greeting allowed."""

    def test_no_case_greeting_allowed(self):
        """Without an active case, a greeting message should still be 'greeting'."""
        result = _fallback_analyze("xin chào", {}, {})
        assert result.message_type == "greeting"

    def test_thank_you_no_active_case_is_still_thank_you(self):
        """'cảm ơn' without active case → still 'thank_you' (not greeting)."""
        result = _fallback_analyze("cảm ơn", {}, {})
        assert result.message_type == "thank_you"


# ─── Response composer: acknowledge_current_case strategy ──────


class TestAcknowledgementResponse:
    """compose_acknowledgement_response produces correct output by case state."""

    def test_unresolved_case(self):
        """Unresolved case → recorded / continue checking."""
        reply = compose_acknowledgement_response(
            resolution_status="",
            workflow="wallet_topup",
        )
        assert "cảm ơn" in reply.lower() or "ghi nhận" in reply.lower()
        assert "pin" in reply.lower() or "otp" in reply.lower()
        assert "xin chào" not in reply.lower()  # No greeting

    def test_resolved_case(self):
        """Resolved case → can message again."""
        reply = compose_acknowledgement_response(
            resolution_status="resolved",
            workflow="wallet_topup",
        )
        assert "ghi nhận" in reply.lower()
        assert "gửi tiếp" in reply.lower() or "hỗ trợ thêm" in reply.lower()

    def test_no_match_case(self):
        """No match case → send reference/receipt."""
        reply = compose_acknowledgement_response(
            resolution_status="no_match",
            workflow="wallet_topup",
        )
        assert any(kw in reply.lower() for kw in ["biên lai", "mã tham chiếu"])

    def test_response_never_empty(self):
        """Response should never be empty."""
        for status in ("", "resolved", "no_match", "processing"):
            reply = compose_acknowledgement_response(
                resolution_status=status,
                workflow="wallet_topup",
            )
            assert len(reply) > 10

    def test_response_contains_safety_reminder(self):
        """All responses must include PIN/OTP safety reminder."""
        for status in ("", "resolved", "no_match"):
            reply = compose_acknowledgement_response(
                resolution_status=status,
                workflow="wallet_topup",
            )
            assert "pin" in reply.lower() or "otp" in reply.lower()


# ─── Long messages with ack words → NOT thank_you ─────────────


class TestLongMessageNotAck:
    """Long messages that mention 'cảm ơn' but describe a problem should NOT
    be classified as thank_you."""

    def test_long_message_with_cam_on(self):
        """A complaint that happens to say 'cảm ơn' is not an ack."""
        msg = "cảm ơn bạn đã giúp đỡ, nhưng tiền tôi nạp 500k vẫn chưa vào ví, mã giao dịch TXN123"
        extracted = ExtractedFields(amount=500000, transaction_id="TXN123")
        msg_type, _, _ = _fallback_classify(
            msg, extracted, has_active_case=True, awaiting_field="",
        )
        # Has financial signal → should NOT be classified as thank_you
        assert msg_type != "thank_you"

    def test_very_long_ack_not_classified(self):
        """A very long message should not be classified as thank_you even if
        it starts with an ack word."""
        msg = "ok, tôi hiểu rồi, nhưng mà tôi muốn hỏi thêm về việc nạp tiền 500k hôm qua mà chưa vào ví"
        extracted = ExtractedFields(amount=500000)
        msg_type, _, _ = _fallback_classify(
            msg, extracted, has_active_case=True, awaiting_field="",
        )
        # Has financial info → not a simple ack
        assert msg_type != "thank_you"
