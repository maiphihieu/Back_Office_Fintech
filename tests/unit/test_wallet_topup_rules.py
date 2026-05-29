"""Unit tests for wallet topup decision rules.

Tests the deterministic rule matrix defined in wallet_topup_rules.py:
  - conflict → manual_review
  - no transaction → manual_review
  - transaction not pending → draft_customer_response
  - no reconciliation → manual_review
  - bank success + money received → create_force_success_draft (approval required)
  - bank success + money NOT received → manual_review
  - bank failed → draft_customer_response
  - fallback → manual_review
"""

import pytest

from fintech_agent.rules.wallet_topup_rules import WalletTopupDecision, decide_wallet_topup
from fintech_agent.schemas.enums import ActionType
from fintech_agent.schemas.evidence import (
    EvidenceBundle,
    EvidenceConflict,
    ReconciliationStatus,
    Transaction,
)


def _txn(status: str = "pending") -> Transaction:
    return Transaction(
        transaction_id="TXN_TOPUP_001",
        user_id="U_TOPUP_001",
        service_type="wallet_topup",
        amount=500000,
        status=status,
    )


def _recon(
    bank_status: str = "success",
    money_received: bool = True,
) -> ReconciliationStatus:
    return ReconciliationStatus(
        transaction_id="TXN_TOPUP_001",
        status="matched",
        mismatch_type="bank_success_wallet_pending",
        bank_status=bank_status,
        bank_amount=500000,
        money_received_in_master_wallet=money_received,
        bank_ref_id="BANK_REF_001",
    )


class TestConflictCase:
    def test_conflict_goes_to_manual_review(self) -> None:
        evidence = EvidenceBundle(
            transaction=_txn(),
            reconciliation_status=_recon(),
            conflicts=[
                EvidenceConflict(
                    source_a="txn", source_b="recon",
                    field="amount", value_a="500000", value_b="300000",
                    description="amount mismatch",
                )
            ],
        )
        d = decide_wallet_topup(_txn(), _recon(), evidence)
        assert d.action == ActionType.MANUAL_REVIEW
        assert d.approval_required is True


class TestNoTransaction:
    def test_no_transaction_manual_review(self) -> None:
        d = decide_wallet_topup(None, _recon(), EvidenceBundle())
        assert d.action == ActionType.MANUAL_REVIEW
        assert d.approval_required is True


class TestTransactionNotPending:
    def test_completed_gets_response(self) -> None:
        d = decide_wallet_topup(_txn("completed"), _recon(), EvidenceBundle())
        assert d.action == ActionType.DRAFT_CUSTOMER_RESPONSE
        assert d.approval_required is False

    def test_failed_gets_response(self) -> None:
        d = decide_wallet_topup(_txn("failed"), _recon(), EvidenceBundle())
        assert d.action == ActionType.DRAFT_CUSTOMER_RESPONSE
        assert d.approval_required is False


class TestNoReconciliation:
    def test_no_recon_manual_review(self) -> None:
        d = decide_wallet_topup(_txn(), None, EvidenceBundle())
        assert d.action == ActionType.MANUAL_REVIEW
        assert d.approval_required is True


class TestBankSuccessMoneyReceived:
    """The happy path for use case 1: force success draft."""

    def test_force_success_draft(self) -> None:
        d = decide_wallet_topup(
            _txn(), _recon(bank_status="success", money_received=True),
            EvidenceBundle(),
        )
        assert d.action == ActionType.CREATE_FORCE_SUCCESS_DRAFT
        assert d.approval_required is True
        assert "bank_success" in d.diagnosis

    def test_force_success_is_high_risk(self) -> None:
        from fintech_agent.rules.risk_rules import classify_risk
        from fintech_agent.schemas.enums import RiskLevel
        risk = classify_risk(ActionType.CREATE_FORCE_SUCCESS_DRAFT, 500000)
        assert risk == RiskLevel.HIGH

    def test_force_success_requires_approval(self) -> None:
        from fintech_agent.rules.risk_rules import requires_approval
        assert requires_approval(ActionType.CREATE_FORCE_SUCCESS_DRAFT, 500000) is True


class TestBankSuccessMoneyNotReceived:
    def test_manual_review(self) -> None:
        d = decide_wallet_topup(
            _txn(), _recon(bank_status="success", money_received=False),
            EvidenceBundle(),
        )
        assert d.action == ActionType.MANUAL_REVIEW
        assert d.approval_required is True


class TestBankFailed:
    def test_bank_failed_response(self) -> None:
        d = decide_wallet_topup(
            _txn(), _recon(bank_status="failed", money_received=False),
            EvidenceBundle(),
        )
        assert d.action == ActionType.DRAFT_CUSTOMER_RESPONSE
        assert d.approval_required is False

    def test_bank_rejected_response(self) -> None:
        d = decide_wallet_topup(
            _txn(), _recon(bank_status="rejected", money_received=False),
            EvidenceBundle(),
        )
        assert d.action == ActionType.DRAFT_CUSTOMER_RESPONSE
        assert d.approval_required is False


class TestFallback:
    def test_unknown_bank_status_manual_review(self) -> None:
        d = decide_wallet_topup(
            _txn(), _recon(bank_status="unknown", money_received=None),
            EvidenceBundle(),
        )
        assert d.action == ActionType.MANUAL_REVIEW
        assert d.approval_required is True
