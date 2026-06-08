"""Tests for Phase 3: merchant settlement rule engine.

Tests all 13 decision branches using constructed evidence bundles.
"""

import pytest
from datetime import date, timedelta

from fintech_agent.rules.merchant_settlement_rules import (
    decide_merchant_settlement,
    MerchantSettlementDecision,
)
from fintech_agent.schemas.enums import ActionType
from fintech_agent.schemas.evidence import (
    BankTransferReceipt,
    EvidenceBundle,
    MerchantBankAccount,
    MerchantPayout,
    MerchantProfile,
    MerchantSettlementLedger,
    SettlementBatch,
)


def _merchant(status="active", merchant_id="MRC_TEST"):
    return MerchantProfile(
        merchant_id=merchant_id, merchant_name="Test Shop",
        status=status, settlement_cycle="D+1",
    )


def _bank_account(verification_status="verified", is_active=True, failure_reason=None):
    return MerchantBankAccount(
        bank_account_id="BA_001", merchant_id="MRC_TEST",
        bank_code="VCB", bank_name="Vietcombank",
        account_number="123456789", account_holder_name="Test",
        verification_status=verification_status, is_active=is_active,
        failure_reason=failure_reason,
    )


def _ledger(net_amount=500000, due_date=None, status="finalized"):
    return MerchantSettlementLedger(
        ledger_id="LED_001", merchant_id="MRC_TEST",
        settlement_date="2026-05-30", due_date=due_date,
        gross_amount=600000, fee_amount=50000,
        refund_amount=30000, chargeback_amount=20000,
        net_settlement_amount=net_amount,
        currency="VND", status=status,
    )


def _payout(status="pending", amount=500000, failure_reason=None, bank_transfer_ref=None):
    return MerchantPayout(
        payout_id="PAY_001", batch_id="BATCH_001", merchant_id="MRC_TEST",
        settlement_date="2026-05-30", amount=amount, currency="VND",
        status=status, failure_reason=failure_reason,
        bank_transfer_ref=bank_transfer_ref,
    )


def _batch(status="completed"):
    return SettlementBatch(
        batch_id="BATCH_001", settlement_date="2026-05-30",
        cycle="D+1", status=status,
    )


def _receipt(sent_to_merchant=False, unc_number="UNC_001"):
    return BankTransferReceipt(
        receipt_id="REC_001", payout_id="PAY_001",
        bank_transfer_ref="BTR_001", bank_status="completed",
        unc_number=unc_number, sent_to_merchant=sent_to_merchant,
    )


# ═══════════════════════════════════════════════════════════
# Case 1: Merchant not found
# ═══════════════════════════════════════════════════════════

class TestMerchantNotFound:
    def test_action(self):
        evidence = EvidenceBundle()  # no merchant
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.REQUEST_IDENTITY_CORRECTION
        assert "merchant_not_found" in d.diagnosis
        assert d.approval_required is False


# ═══════════════════════════════════════════════════════════
# Case 2: Merchant on_hold
# ═══════════════════════════════════════════════════════════

class TestMerchantOnHold:
    def test_action(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(status="on_hold"),
            merchant_bank_account=_bank_account(),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.MANUAL_SETTLEMENT_REVIEW
        assert "on_hold" in d.diagnosis


# ═══════════════════════════════════════════════════════════
# Case 3: Invalid bank account (MRC_003_INVALID_BANK)
# ═══════════════════════════════════════════════════════════

class TestBankAccountInvalid:
    def test_pending_verification(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(verification_status="pending"),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.REQUEST_BANK_ACCOUNT_CORRECTION
        assert "verification_pending" in d.diagnosis

    def test_inactive(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(is_active=False),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.REQUEST_BANK_ACCOUNT_CORRECTION
        assert "inactive" in d.diagnosis

    def test_name_mismatch(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(verification_status="name_mismatch"),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.REQUEST_BANK_ACCOUNT_CORRECTION
        assert "name_mismatch" in d.diagnosis


# ═══════════════════════════════════════════════════════════
# Case 4: Missing ledger
# ═══════════════════════════════════════════════════════════

class TestMissingLedger:
    def test_action(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.MANUAL_SETTLEMENT_REVIEW
        assert "ledger_not_found" in d.diagnosis


# ═══════════════════════════════════════════════════════════
# Case 5: Not due yet
# ═══════════════════════════════════════════════════════════

class TestNotDueYet:
    def test_future_due_date(self):
        future = (date.today() + timedelta(days=5)).isoformat()
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(due_date=future),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.DRAFT_CUSTOMER_RESPONSE
        assert "not_due_yet" in d.diagnosis


# ═══════════════════════════════════════════════════════════
# Case 6: Net settlement amount <= 0 (MRC_008_ZERO_NET)
# ═══════════════════════════════════════════════════════════

class TestZeroNetAmount:
    def test_zero(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(net_amount=0),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.DRAFT_CUSTOMER_RESPONSE
        assert "zero_or_negative" in d.diagnosis

    def test_negative(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(net_amount=-100),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.DRAFT_CUSTOMER_RESPONSE


# ═══════════════════════════════════════════════════════════
# Case 7: Batch failed → manual payout draft (MRC_001_BATCH_FAIL)
# ═══════════════════════════════════════════════════════════

class TestBatchFailed:
    def test_no_payout_batch_failed(self):
        """Batch failed, no payout exists → create manual payout draft."""
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(net_amount=500000),
            settlement_batch=_batch(status="failed"),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.CREATE_MANUAL_PAYOUT_DRAFT
        assert d.approval_required is True
        assert "batch_failed" in d.diagnosis

    def test_payout_failed_batch_failed(self):
        """Batch failed AND payout also failed → manual payout draft."""
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(net_amount=500000),
            merchant_payout=_payout(status="failed", failure_reason="batch_error"),
            settlement_batch=_batch(status="failed"),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.CREATE_MANUAL_PAYOUT_DRAFT
        assert d.approval_required is True


# ═══════════════════════════════════════════════════════════
# Case 8: Payout failed with retriable error
# ═══════════════════════════════════════════════════════════

class TestPayoutFailedRetriable:
    def test_timeout(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(),
            merchant_payout=_payout(status="failed", failure_reason="bank_timeout"),
            settlement_batch=_batch(status="completed"),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.CREATE_MANUAL_PAYOUT_DRAFT
        assert d.approval_required is True
        assert "retriable" in d.diagnosis

    def test_system_error(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(),
            merchant_payout=_payout(status="failed", failure_reason="system_error_503"),
            settlement_batch=_batch(status="completed"),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.CREATE_MANUAL_PAYOUT_DRAFT


# ═══════════════════════════════════════════════════════════
# Case 9: Payout in progress → monitor (MRC_005_BANK_PENDING)
# ═══════════════════════════════════════════════════════════

class TestPayoutInProgress:
    def test_processing(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(),
            merchant_payout=_payout(status="processing"),
            settlement_batch=_batch(),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.DRAFT_CUSTOMER_RESPONSE
        assert "in_progress" in d.diagnosis
        assert d.details["duplicate_payout_risk"] is True

    def test_pending(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(),
            merchant_payout=_payout(status="pending"),
            settlement_batch=_batch(),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.DRAFT_CUSTOMER_RESPONSE


# ═══════════════════════════════════════════════════════════
# Case 10: Payout success + UNC sent (MRC_006_SUCCESS_UNC_SENT)
# ═══════════════════════════════════════════════════════════

class TestPayoutSuccessUNCSent:
    def test_unc_sent(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(),
            merchant_payout=_payout(status="success"),
            settlement_batch=_batch(),
            bank_transfer_receipt=_receipt(sent_to_merchant=True),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.DRAFT_CUSTOMER_RESPONSE
        assert "unc_already_sent" in d.diagnosis
        assert d.approval_required is False


# ═══════════════════════════════════════════════════════════
# Case 11: Payout success + UNC not sent
# ═══════════════════════════════════════════════════════════

class TestPayoutSuccessUNCNotSent:
    def test_unc_not_sent(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(),
            merchant_payout=_payout(status="success"),
            settlement_batch=_batch(),
            bank_transfer_receipt=_receipt(sent_to_merchant=False),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.SEND_UNC_EMAIL_DRAFT
        assert d.approval_required is True


# ═══════════════════════════════════════════════════════════
# Case 12: Amount mismatch (MRC_013_AMOUNT_MISMATCH)
# ═══════════════════════════════════════════════════════════

class TestAmountMismatch:
    def test_partial_payout(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(net_amount=500000),
            merchant_payout=_payout(status="success", amount=300000),
            settlement_batch=_batch(),
            bank_transfer_receipt=_receipt(sent_to_merchant=True),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.MANUAL_SETTLEMENT_REVIEW
        assert "difference" in d.diagnosis or "mismatch" in d.diagnosis
        assert d.approval_required is True
        assert d.details["difference"] == 200000


# ═══════════════════════════════════════════════════════════
# Case 13: Unknown evidence → manual review
# ═══════════════════════════════════════════════════════════

class TestUnknownEvidence:
    def test_no_payout_batch_ok(self):
        """Batch completed but no payout → weird state → manual review."""
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(),
            settlement_batch=_batch(status="completed"),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.MANUAL_SETTLEMENT_REVIEW


# ═══════════════════════════════════════════════════════════
# Safety: Never payout if bank is invalid
# ═══════════════════════════════════════════════════════════

class TestSafetyBankCheck:
    def test_bank_invalid_even_with_failed_batch(self):
        """Bank invalid should block payout even if batch failed."""
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(verification_status="rejected"),
            merchant_settlement_ledger=_ledger(),
            settlement_batch=_batch(status="failed"),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.REQUEST_BANK_ACCOUNT_CORRECTION
        # Must NOT be CREATE_MANUAL_PAYOUT_DRAFT
        assert d.action != ActionType.CREATE_MANUAL_PAYOUT_DRAFT


# ═══════════════════════════════════════════════════════════
# Safety: Never duplicate payout
# ═══════════════════════════════════════════════════════════

class TestSafetyNoDuplicatePayout:
    def test_processing_payout_no_manual(self):
        """Processing payout → must NOT create manual payout."""
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(),
            merchant_payout=_payout(status="processing"),
            settlement_batch=_batch(status="completed"),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action != ActionType.CREATE_MANUAL_PAYOUT_DRAFT
        assert d.details["duplicate_payout_risk"] is True

    def test_success_payout_no_manual(self):
        """Success payout → must NOT create manual payout."""
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(),
            merchant_payout=_payout(status="success"),
            settlement_batch=_batch(status="completed"),
            bank_transfer_receipt=_receipt(sent_to_merchant=True),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action != ActionType.CREATE_MANUAL_PAYOUT_DRAFT


# ═══════════════════════════════════════════════════════════
# Safety: All manual payouts are draft_only + approval_required
# ═══════════════════════════════════════════════════════════

class TestSafetyDraftOnly:
    def test_batch_fail_draft_only(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(net_amount=500000),
            settlement_batch=_batch(status="failed"),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.CREATE_MANUAL_PAYOUT_DRAFT
        assert d.approval_required is True
        assert d.details["draft_only"] is True

    def test_retriable_fail_draft_only(self):
        evidence = EvidenceBundle(
            merchant_profile=_merchant(),
            merchant_bank_account=_bank_account(),
            merchant_settlement_ledger=_ledger(),
            merchant_payout=_payout(status="failed", failure_reason="bank_timeout"),
            settlement_batch=_batch(status="completed"),
        )
        d = decide_merchant_settlement(evidence)
        assert d.action == ActionType.CREATE_MANUAL_PAYOUT_DRAFT
        assert d.approval_required is True
        assert d.details["draft_only"] is True


# ═══════════════════════════════════════════════════════════
# Dispatch integration: rule_decision routes correctly
# ═══════════════════════════════════════════════════════════

class TestRuleDecisionDispatch:
    def test_merchant_workflow_dispatches(self):
        """Verify rule_decision.py dispatches to merchant rules."""
        from fintech_agent.nodes.rule_decision import apply_rules
        state = {
            "selected_workflow": "merchant_settlement_delay",
            "evidence_bundle": EvidenceBundle(
                merchant_profile=_merchant(),
                merchant_bank_account=_bank_account(),
                merchant_settlement_ledger=_ledger(net_amount=500000),
                settlement_batch=_batch(status="failed"),
            ),
            "case_id": "CASE-DISPATCH-001",
        }
        result = apply_rules(state)
        assert result["rule_decision"]["action"] == "create_manual_payout_draft"
        assert result["approval_required"] is True

    def test_existing_train_ticket_not_broken(self):
        """Existing workflow dispatch still works."""
        from fintech_agent.nodes.rule_decision import apply_rules
        from fintech_agent.schemas.evidence import Transaction, WalletLedger
        state = {
            "selected_workflow": "wallet_topup",
            "evidence_bundle": EvidenceBundle(
                transaction=Transaction(
                    transaction_id="TXN_001", user_id="U_001",
                    service_type="wallet_topup", amount=100000, status="pending",
                ),
            ),
            "case_id": "CASE-EXISTING-001",
        }
        result = apply_rules(state)
        # Should not crash; may go to manual_review since no reconciliation
        assert "rule_decision" in result or "status" in result
