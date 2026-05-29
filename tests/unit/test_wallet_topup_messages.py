"""Tests for wallet topup customer-facing messages.

Verifies that diagnosis codes are mapped to human-readable messages
for CS/Ops staff and customers, without changing rule logic.
"""

from __future__ import annotations

import pytest

from fintech_agent.messages.wallet_topup_messages import (
    BANK_FAILED_CUSTOMER_MESSAGE,
    FORCE_SUCCESS_CS_MESSAGE,
    MANUAL_REVIEW_CS_MESSAGE,
    get_cs_message,
    get_customer_message,
)
from fintech_agent.schemas.enums import ActionType


# ─── get_customer_message ────────────────────────────────────


class TestGetCustomerMessage:
    """Test customer-facing message mapping."""

    def test_bank_failed_has_refund_timeframe(self):
        """Bank failed → message contains '3–5 ngày làm việc'."""
        msg = get_customer_message("bank_transfer_failed_wait_reversal")
        assert "hoàn tiền trong 3–5 ngày làm việc" in msg

    def test_bank_failed_message_matches_constant(self):
        msg = get_customer_message("bank_transfer_failed_wait_reversal")
        assert msg == BANK_FAILED_CUSTOMER_MESSAGE

    def test_money_not_received_has_refund_timeframe(self):
        """Money not received → same customer message with refund timeframe."""
        msg = get_customer_message("money_not_received_in_master_wallet")
        assert "hoàn tiền trong 3–5 ngày làm việc" in msg

    def test_transaction_not_pending_prefix_match(self):
        """Transaction not pending (with status suffix) → matched by prefix."""
        msg = get_customer_message("transaction_not_pending (status=completed)")
        assert "đã được xử lý" in msg

    def test_unknown_diagnosis_falls_back(self):
        """Unknown diagnosis → generic fallback."""
        msg = get_customer_message("some_unknown_diagnosis")
        assert msg == "Kết quả kiểm tra: some_unknown_diagnosis"


# ─── get_cs_message ──────────────────────────────────────────


class TestGetCsMessage:
    """Test internal CS/Ops message mapping."""

    def test_force_success_has_draft_and_approval(self):
        """Force success → CS message mentions 'draft Force-Success' and 'cần nhân viên phê duyệt'."""
        msg = get_cs_message(ActionType.CREATE_FORCE_SUCCESS_DRAFT, "bank_success_money_received_wallet_pending")
        assert "draft Force-Success" in msg
        assert "cần nhân viên phê duyệt" in msg

    def test_force_success_matches_constant(self):
        msg = get_cs_message(ActionType.CREATE_FORCE_SUCCESS_DRAFT, "any_diagnosis")
        assert msg == FORCE_SUCCESS_CS_MESSAGE

    def test_manual_review_blocks_auto_force_success(self):
        """Manual review → CS message mentions 'Không được Force-Success tự động' and 'manual review'."""
        msg = get_cs_message(ActionType.MANUAL_REVIEW, "bank_success_but_money_not_in_master_wallet")
        assert "Không được Force-Success tự động" in msg
        assert "manual review" in msg

    def test_manual_review_matches_constant(self):
        msg = get_cs_message(ActionType.MANUAL_REVIEW, "any_diagnosis")
        assert msg == MANUAL_REVIEW_CS_MESSAGE

    def test_customer_response_uses_customer_message(self):
        """DRAFT_CUSTOMER_RESPONSE → uses customer-facing message."""
        msg = get_cs_message(ActionType.DRAFT_CUSTOMER_RESPONSE, "bank_transfer_failed_wait_reversal")
        assert "hoàn tiền trong 3–5 ngày làm việc" in msg

    def test_unknown_action_falls_back(self):
        """Unknown action → generic fallback."""
        msg = get_cs_message(ActionType.NO_ACTION, "some_diag")
        assert "Kết quả kiểm tra: some_diag" in msg
