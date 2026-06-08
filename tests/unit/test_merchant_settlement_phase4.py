"""Tests for merchant settlement Phase 4: drafts, tickets, messages, safety.

Covers:
  - ManualPayoutDraft is draft_only + approval_required
  - Invalid bank account → no payout draft
  - Processing payout → no duplicate payout
  - Success payout → UNC/reference action only
  - Zero net → statement action only
  - Ticket builder produces correct actions
  - Response generator produces safe messages
  - Existing workflows still pass
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fintech_agent.schemas.actions import (
    ManualPayoutDraft,
    UncEmailDraft,
    BankAccountCorrectionDraft,
    SettlementStatementDraft,
    MerchantEmailDraft,
)
from fintech_agent.schemas.enums import ActionType, CaseStatus, RiskLevel
from fintech_agent.schemas.evidence import (
    EvidenceBundle,
    MerchantBankAccount,
    MerchantPayout,
    MerchantProfile,
    MerchantSettlementLedger,
    SettlementBatch,
    BankTransferReceipt,
)
from fintech_agent.messages.merchant_settlement_messages import (
    get_cs_message,
    get_merchant_message,
)
from fintech_agent.rules.merchant_settlement_rules import (
    MerchantSettlementDecision,
    decide_merchant_settlement,
)


def _make_evidence(**overrides) -> EvidenceBundle:
    """Build evidence bundle with merchant settlement data."""
    defaults = dict(
        merchant_profile=MerchantProfile(
            merchant_id="M001", merchant_name="Shop ABC",
            status="active", settlement_cycle="D+1",
        ),
        merchant_bank_account=MerchantBankAccount(
            bank_account_id="BA001", merchant_id="M001",
            bank_name="VCB", account_number="123456789",
            account_holder="Shop ABC", verification_status="verified",
            is_active=True,
        ),
        merchant_settlement_ledger=MerchantSettlementLedger(
            ledger_id="L001", merchant_id="M001",
            settlement_date="2025-01-15", due_date="2025-01-10",
            gross_amount=10_000_000, fee_amount=200_000,
            refund_amount=0, chargeback_amount=0,
            net_settlement_amount=9_800_000, currency="VND",
        ),
        settlement_batch=SettlementBatch(
            batch_id="B001", merchant_id="M001",
            settlement_date="2025-01-15", status="completed",
        ),
    )
    defaults.update(overrides)
    return EvidenceBundle(**defaults)


def _make_recommended_action(action_type, diagnosis, approval_required=False):
    """Create a mock recommended action."""
    action = MagicMock()
    action.action_type = action_type
    action.diagnosis = diagnosis
    action.risk_level = RiskLevel.MEDIUM
    action.approval_required = approval_required
    action.details = {}
    return action


# ═══════════════════════════════════════════════════════════════
#  Schema tests
# ═══════════════════════════════════════════════════════════════


class TestManualPayoutDraftSchema(unittest.TestCase):
    """ManualPayoutDraft must always be draft_only and approval_required."""

    def test_draft_only_default(self):
        d = ManualPayoutDraft(
            case_id="C001", merchant_id="M001",
            amount=9_800_000, reason="batch_failed",
        )
        self.assertEqual(d.execution_mode, "draft_only")

    def test_approval_required_default(self):
        d = ManualPayoutDraft(
            case_id="C001", merchant_id="M001",
            amount=9_800_000, reason="batch_failed",
        )
        self.assertTrue(d.approval_required)

    def test_trusted_amount_source_default(self):
        d = ManualPayoutDraft(
            case_id="C001", merchant_id="M001",
            amount=9_800_000, reason="batch_failed",
        )
        self.assertIn("settlement_ledger", d.trusted_amount_source)

    def test_safety_notes_not_empty(self):
        d = ManualPayoutDraft(
            case_id="C001", merchant_id="M001",
            amount=9_800_000, reason="batch_failed",
        )
        self.assertTrue(len(d.safety_notes) > 0)

    def test_duplicate_payout_risk_default(self):
        d = ManualPayoutDraft(
            case_id="C001", merchant_id="M001",
            amount=9_800_000, reason="batch_failed",
        )
        self.assertFalse(d.duplicate_payout_risk)


class TestUncEmailDraftSchema(unittest.TestCase):
    def test_draft_only(self):
        d = UncEmailDraft(case_id="C001", merchant_id="M001")
        self.assertEqual(d.execution_mode, "draft_only")


class TestBankAccountCorrectionDraftSchema(unittest.TestCase):
    def test_draft_only(self):
        d = BankAccountCorrectionDraft(
            case_id="C001", merchant_id="M001",
            correction_reason="name_mismatch",
        )
        self.assertEqual(d.execution_mode, "draft_only")


class TestSettlementStatementDraftSchema(unittest.TestCase):
    def test_draft_only(self):
        d = SettlementStatementDraft(case_id="C001", merchant_id="M001")
        self.assertEqual(d.execution_mode, "draft_only")


class TestMerchantEmailDraftSchema(unittest.TestCase):
    def test_draft_only(self):
        d = MerchantEmailDraft(case_id="C001", merchant_id="M001")
        self.assertEqual(d.execution_mode, "draft_only")


# ═══════════════════════════════════════════════════════════════
#  Draft action handler tests (integration via draft_action.py)
# ═══════════════════════════════════════════════════════════════


class TestDraftActionInvalidBank(unittest.TestCase):
    """Invalid bank account must NOT create payout draft."""

    def test_bank_pending_no_payout(self):
        ev = _make_evidence(
            merchant_bank_account=MerchantBankAccount(
                bank_account_id="BA001", merchant_id="M001",
                bank_name="VCB", account_number="123",
                account_holder="X", verification_status="pending",
                is_active=True,
            ),
        )
        decision = decide_merchant_settlement(ev)
        self.assertEqual(decision.action, ActionType.REQUEST_BANK_ACCOUNT_CORRECTION)
        self.assertNotEqual(decision.action, ActionType.CREATE_MANUAL_PAYOUT_DRAFT)


class TestDraftActionProcessingPayout(unittest.TestCase):
    """Processing payout must NOT create duplicate payout."""

    def test_processing_no_duplicate(self):
        ev = _make_evidence(
            merchant_payout=MerchantPayout(
                payout_id="P001", merchant_id="M001",
                amount=9_800_000, status="processing",
            ),
        )
        decision = decide_merchant_settlement(ev)
        self.assertEqual(decision.action, ActionType.DRAFT_CUSTOMER_RESPONSE)
        self.assertNotEqual(decision.action, ActionType.CREATE_MANUAL_PAYOUT_DRAFT)


class TestDraftActionSuccessPayout(unittest.TestCase):
    """Success payout must create UNC action, NOT payout."""

    def test_success_creates_unc_draft(self):
        ev = _make_evidence(
            merchant_payout=MerchantPayout(
                payout_id="P001", merchant_id="M001",
                amount=9_800_000, status="success",
            ),
            bank_transfer_receipt=BankTransferReceipt(
                receipt_id="R001", payout_id="P001",
                unc_number="UNC123", sent_to_merchant=False,
            ),
        )
        decision = decide_merchant_settlement(ev)
        self.assertEqual(decision.action, ActionType.SEND_UNC_EMAIL_DRAFT)
        self.assertNotEqual(decision.action, ActionType.CREATE_MANUAL_PAYOUT_DRAFT)


class TestDraftActionZeroNet(unittest.TestCase):
    """Zero/negative net amount → customer response (statement), no payout."""

    def test_zero_net_no_payout(self):
        ev = _make_evidence(
            merchant_settlement_ledger=MerchantSettlementLedger(
                ledger_id="L001", merchant_id="M001",
                settlement_date="2025-01-15", due_date="2025-01-10",
                gross_amount=500_000, fee_amount=500_000,
                net_settlement_amount=0, currency="VND",
            ),
        )
        decision = decide_merchant_settlement(ev)
        self.assertEqual(decision.action, ActionType.DRAFT_CUSTOMER_RESPONSE)
        self.assertNotEqual(decision.action, ActionType.CREATE_MANUAL_PAYOUT_DRAFT)


# ═══════════════════════════════════════════════════════════════
#  Draft action node integration
# ═══════════════════════════════════════════════════════════════


class TestDraftActionNodeManualPayout(unittest.TestCase):
    """Test that draft action node produces correct manual payout output."""

    def test_manual_payout_draft_output(self):
        from fintech_agent.nodes.draft_action import _draft_manual_payout
        ev = _make_evidence(
            settlement_batch=SettlementBatch(
                batch_id="B001", merchant_id="M001",
                settlement_date="2025-01-15", status="failed",
            ),
        )
        action = _make_recommended_action(
            ActionType.CREATE_MANUAL_PAYOUT_DRAFT,
            "batch_failed_create_manual_payout",
            approval_required=True,
        )
        result = _draft_manual_payout({}, action, ev, "CASE001", None, [])
        draft = result["draft_output"]

        self.assertEqual(draft["type"], "manual_payout_draft")
        self.assertEqual(draft["execution_mode"], "draft_only")
        self.assertTrue(draft["approval_required"])
        self.assertEqual(draft["amount"], 9_800_000)
        self.assertEqual(draft["trusted_amount_source"], "settlement_ledger.net_settlement_amount")
        self.assertEqual(result["status"], CaseStatus.DRAFT_CREATED)
        self.assertTrue(result["approval_required"])


class TestDraftActionNodeUncEmail(unittest.TestCase):
    """Test UNC email draft output."""

    def test_unc_email_draft_output(self):
        from fintech_agent.nodes.draft_action import _draft_unc_email
        ev = _make_evidence(
            merchant_payout=MerchantPayout(
                payout_id="P001", merchant_id="M001",
                amount=9_800_000, status="success",
            ),
            bank_transfer_receipt=BankTransferReceipt(
                receipt_id="R001", payout_id="P001",
                unc_number="UNC456", sent_to_merchant=False,
                receipt_url="https://example.com/unc.pdf",
            ),
        )
        action = _make_recommended_action(
            ActionType.SEND_UNC_EMAIL_DRAFT,
            "payout_success_unc_not_sent",
            approval_required=True,
        )
        result = _draft_unc_email({}, action, ev, "CASE001", None, [])
        draft = result["draft_output"]

        self.assertEqual(draft["type"], "unc_email_draft")
        self.assertEqual(draft["execution_mode"], "draft_only")
        self.assertEqual(draft["unc_number"], "UNC456")
        self.assertEqual(result["status"], CaseStatus.DRAFT_CREATED)


class TestDraftActionNodeBankCorrection(unittest.TestCase):
    """Test bank account correction draft output."""

    def test_bank_correction_draft_output(self):
        from fintech_agent.nodes.draft_action import _draft_bank_account_correction
        ev = _make_evidence(
            merchant_bank_account=MerchantBankAccount(
                bank_account_id="BA001", merchant_id="M001",
                bank_name="VCB", account_number="123",
                account_holder="X", verification_status="pending",
                is_active=True,
            ),
        )
        action = _make_recommended_action(
            ActionType.REQUEST_BANK_ACCOUNT_CORRECTION,
            "bank_account_verification_pending",
        )
        result = _draft_bank_account_correction({}, action, ev, "CASE001", None, [])
        draft = result["draft_output"]

        self.assertEqual(draft["type"], "bank_account_correction_draft")
        self.assertEqual(draft["execution_mode"], "draft_only")
        self.assertEqual(result["status"], CaseStatus.DRAFT_CREATED)


# ═══════════════════════════════════════════════════════════════
#  Message tests
# ═══════════════════════════════════════════════════════════════


class TestMerchantSettlementMessages(unittest.TestCase):
    """Messages module returns correct CS and merchant messages."""

    def test_cs_message_batch_failed(self):
        msg = get_cs_message(
            ActionType.CREATE_MANUAL_PAYOUT_DRAFT,
            "batch_failed_create_manual_payout",
        )
        self.assertIn("batch", msg.lower())
        self.assertIn("settlement", msg.lower())

    def test_merchant_message_batch_failed(self):
        msg = get_merchant_message("batch_failed_create_manual_payout")
        self.assertIn("settlement", msg.lower())
        # Must not contain internal error codes
        self.assertNotIn("batch_failed", msg.lower())
        self.assertNotIn("create_manual_payout", msg.lower())

    def test_merchant_message_bank_pending(self):
        msg = get_merchant_message("bank_account_verification_pending")
        self.assertIn("ngân hàng", msg.lower())

    def test_merchant_message_unc(self):
        msg = get_merchant_message("payout_success_unc_not_sent")
        self.assertIn("unc", msg.lower())

    def test_unknown_diagnosis_fallback(self):
        msg = get_merchant_message("some_unknown_code")
        self.assertIn("ghi nhận", msg.lower())

    def test_merchant_message_no_sensitive_data(self):
        """Merchant messages must not expose sensitive terms."""
        for key in (
            "batch_failed_create_manual_payout",
            "payout_failed_retriable_retry_payout",
            "bank_account_verification_rejected",
            "merchant_on_hold_escalate_ops",
        ):
            msg = get_merchant_message(key)
            self.assertNotIn("settlement_ledger", msg.lower())
            self.assertNotIn("rule_engine", msg.lower())
            self.assertNotIn("draft_only", msg.lower())
            self.assertNotIn("api_key", msg.lower())


# ═══════════════════════════════════════════════════════════════
#  Ticket builder integration
# ═══════════════════════════════════════════════════════════════


class TestTicketBuilderMerchantSettlement(unittest.TestCase):
    """Ticket builder correctly handles merchant settlement actions."""

    def test_manual_payout_in_action_map(self):
        from fintech_agent.llm.ticket_builder import _ACTION_MAP
        self.assertIn("create_manual_payout_draft", _ACTION_MAP)
        entry = _ACTION_MAP["create_manual_payout_draft"]
        self.assertEqual(entry["execution_mode"], "draft_only")
        self.assertIn("settlement_ledger", entry["description"])

    def test_unc_email_in_action_map(self):
        from fintech_agent.llm.ticket_builder import _ACTION_MAP
        self.assertIn("send_unc_email_draft", _ACTION_MAP)

    def test_bank_correction_in_action_map(self):
        from fintech_agent.llm.ticket_builder import _ACTION_MAP
        self.assertIn("request_bank_account_correction", _ACTION_MAP)

    def test_manual_settlement_review_in_action_map(self):
        from fintech_agent.llm.ticket_builder import _ACTION_MAP
        self.assertIn("manual_settlement_review", _ACTION_MAP)

    def test_money_action_includes_manual_payout(self):
        from fintech_agent.llm.ticket_builder import _MONEY_ACTION_TYPES
        self.assertIn("create_manual_payout_draft", _MONEY_ACTION_TYPES)

    def test_evidence_fields_include_merchant(self):
        from fintech_agent.llm.ticket_builder import _EVIDENCE_FIELDS
        for key in (
            "merchant_profile", "merchant_bank_account",
            "merchant_settlement_ledger", "merchant_payout",
            "settlement_batch", "bank_transfer_receipt",
        ):
            self.assertIn(key, _EVIDENCE_FIELDS)

    def test_staff_instructions_include_settlement(self):
        from fintech_agent.llm.ticket_builder import _STAFF_INSTRUCTIONS
        for key in (
            "create_manual_payout_draft",
            "send_unc_email_draft",
            "request_bank_account_correction",
            "manual_settlement_review",
        ):
            self.assertIn(key, _STAFF_INSTRUCTIONS)

    def test_manual_settlement_review_is_manual_review_required(self):
        from fintech_agent.llm.ticket_builder import _determine_resolution_status
        status = _determine_resolution_status("manual_settlement_review", [], False)
        self.assertEqual(status, "manual_review_required")


# ═══════════════════════════════════════════════════════════════
#  Amount verification
# ═══════════════════════════════════════════════════════════════


class TestAmountVerificationSettlement(unittest.TestCase):
    """Amount verification uses settlement_ledger as trusted source."""

    def test_settlement_ledger_as_trusted_source(self):
        from fintech_agent.llm.ticket_builder import _build_amount_verification
        state = {
            "evidence_bundle": _make_evidence(),
        }
        av = _build_amount_verification(state)
        self.assertEqual(av.trusted_amount, 9_800_000)
        self.assertEqual(av.trusted_amount_source, "settlement_ledger.net_settlement_amount")


# ═══════════════════════════════════════════════════════════════
#  Response generator
# ═══════════════════════════════════════════════════════════════


class TestResponseGeneratorMerchant(unittest.TestCase):
    """Response generator handles merchant settlement delay."""

    def test_fallback_response_merchant(self):
        from fintech_agent.llm.response_generator import generate_safe_fallback_response
        state = {
            "selected_workflow": "merchant_settlement_delay",
            "recommended_action": _make_recommended_action(
                ActionType.CREATE_MANUAL_PAYOUT_DRAFT,
                "batch_failed_create_manual_payout",
                True,
            ),
        }
        resp = generate_safe_fallback_response(state)
        self.assertIn("giải ngân", resp.case_summary.lower())
        self.assertIn("settlement", resp.customer_reply_draft.lower())
        # Safety notes must mention payout
        safety_text = " ".join(resp.safety_notes).lower()
        self.assertIn("payout", safety_text)


# ═══════════════════════════════════════════════════════════════
#  Existing workflow regression
# ═══════════════════════════════════════════════════════════════


class TestExistingWorkflowsStillPass(unittest.TestCase):
    """Ensure Phase 4 changes don't break existing workflows."""

    def test_wallet_topup_action_map_intact(self):
        from fintech_agent.llm.ticket_builder import _ACTION_MAP
        self.assertIn("create_refund_request_draft", _ACTION_MAP)
        self.assertIn("create_force_success_draft", _ACTION_MAP)
        self.assertIn("create_reconciliation_ticket_draft", _ACTION_MAP)

    def test_fraud_action_map_intact(self):
        from fintech_agent.llm.ticket_builder import _ACTION_MAP
        self.assertIn("create_unlock_account_draft", _ACTION_MAP)
        self.assertIn("create_request_documents_response_draft", _ACTION_MAP)

    def test_existing_enums_unchanged(self):
        # All existing enums should still exist
        for name in (
            "DRAFT_CUSTOMER_RESPONSE", "CREATE_REFUND_REQUEST_DRAFT",
            "CREATE_RECONCILIATION_TICKET_DRAFT", "CREATE_FORCE_SUCCESS_DRAFT",
            "CREATE_UNLOCK_ACCOUNT_DRAFT", "MANUAL_REVIEW", "NO_ACTION",
        ):
            self.assertTrue(hasattr(ActionType, name))

    def test_refund_schema_still_works(self):
        from fintech_agent.schemas.actions import RefundRequestDraft
        d = RefundRequestDraft(
            idempotency_key="test", case_id="C1",
            transaction_id="T1", user_id="U1",
            amount=100_000, reason="test",
            evidence_summary=["test"],
        )
        self.assertEqual(d.amount, 100_000)


if __name__ == "__main__":
    unittest.main()
