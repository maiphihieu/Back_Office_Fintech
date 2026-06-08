"""Tests for DiagnosticEngine — structured bottleneck analysis.

Tests all decision rules for wallet_topup and fraud_account_lock workflows.
"""

from __future__ import annotations

import pytest

from fintech_agent.llm.diagnostic_engine import (
    Bottleneck,
    DiagnosticResult,
    Resolution,
    diagnose,
)
from fintech_agent.schemas.evidence import (
    AccountStatus,
    EvidenceBundle,
    FraudCase,
    ReconciliationStatus,
    Transaction,
    WalletLedger,
)


# ═══════════════════════════════════════════════════════════════
# WALLET TOPUP SCENARIOS
# ═══════════════════════════════════════════════════════════════


class TestWalletTopupRule1BankSuccessMoneyReceivedTxnPending:
    """Rule 1: bank=success + money=true + txn=pending → force_success."""

    def test_bottleneck_is_wallet_system(self):
        eb = EvidenceBundle(
            transaction=Transaction(
                transaction_id="TXN001",
                user_id="U001",
                service_type="topup",
                amount=500_000,
                status="pending",
            ),
            reconciliation_status=ReconciliationStatus(
                transaction_id="TXN001",
                bank_status="success",
                money_received_in_master_wallet=True,
                bank_amount=500_000,
            ),
        )
        result = diagnose("wallet_topup", "bank_success_money_received_wallet_pending", eb)

        assert result.bottleneck.location == "wallet_system"
        assert result.bottleneck.confidence == "high"
        assert result.resolution.recommended_action == "force_success"
        assert result.resolution.approval_required is True
        assert result.missing_data == []

    def test_explanation_mentions_bank_confirmed_and_wallet_pending(self):
        eb = EvidenceBundle(
            transaction=Transaction(
                transaction_id="TXN002",
                user_id="U001",
                service_type="topup",
                amount=1_000_000,
                status="pending",
            ),
            reconciliation_status=ReconciliationStatus(
                transaction_id="TXN002",
                bank_status="success",
                money_received_in_master_wallet=True,
            ),
        )
        result = diagnose("wallet_topup", "bank_success_money_received_wallet_pending", eb)

        assert "Bank" in result.bottleneck.explanation
        assert "PENDING" in result.bottleneck.explanation
        assert "master wallet" in result.bottleneck.explanation

    def test_evidence_contains_data_points(self):
        eb = EvidenceBundle(
            transaction=Transaction(
                transaction_id="TXN003",
                user_id="U001",
                service_type="topup",
                amount=200_000,
                status="pending",
            ),
            reconciliation_status=ReconciliationStatus(
                transaction_id="TXN003",
                bank_status="success",
                money_received_in_master_wallet=True,
            ),
        )
        result = diagnose("wallet_topup", "", eb)

        evidence = result.bottleneck.evidence
        assert any("transaction.status=pending" in e for e in evidence)
        assert any("bank_status=success" in e for e in evidence)
        assert any("money_received_in_master_wallet=True" in e for e in evidence)


class TestWalletTopupRule2TxnSuccessButLedgerMissing:
    """Rule 2: bank=success + money=true + txn=success + no ledger."""

    def test_bottleneck_is_ledger_posting(self):
        eb = EvidenceBundle(
            transaction=Transaction(
                transaction_id="TXN010",
                user_id="U001",
                service_type="topup",
                amount=500_000,
                status="success",
            ),
            reconciliation_status=ReconciliationStatus(
                transaction_id="TXN010",
                bank_status="success",
                money_received_in_master_wallet=True,
            ),
            # wallet_ledger is None
        )
        result = diagnose("wallet_topup", "transaction_not_pending", eb)

        assert result.bottleneck.location == "wallet_system"
        assert "ledger" in result.bottleneck.explanation.lower()
        assert result.resolution.recommended_action == "post_ledger_or_manual_balance_review"
        assert result.resolution.approval_required is True

    def test_txn_success_with_ledger_is_normal(self):
        eb = EvidenceBundle(
            transaction=Transaction(
                transaction_id="TXN011",
                user_id="U001",
                service_type="topup",
                amount=500_000,
                status="success",
            ),
            reconciliation_status=ReconciliationStatus(
                transaction_id="TXN011",
                bank_status="success",
                money_received_in_master_wallet=True,
            ),
            wallet_ledger=WalletLedger(
                transaction_id="TXN011",
                user_id="U001",
                status="debited",
            ),
        )
        result = diagnose("wallet_topup", "transaction_not_pending", eb)

        assert result.bottleneck.location == "wallet_system"
        assert result.resolution.recommended_action == "inform_customer"
        assert result.resolution.approval_required is False


class TestWalletTopupRule3BankPending:
    """Rule 3: bank=pending → bank bottleneck."""

    def test_bank_pending(self):
        eb = EvidenceBundle(
            transaction=Transaction(
                transaction_id="TXN020",
                user_id="U001",
                service_type="topup",
                amount=300_000,
                status="pending",
            ),
            reconciliation_status=ReconciliationStatus(
                transaction_id="TXN020",
                bank_status="pending",
                money_received_in_master_wallet=False,
            ),
        )
        result = diagnose("wallet_topup", "", eb)

        assert result.bottleneck.location == "bank"
        assert result.bottleneck.confidence == "medium"
        assert result.resolution.recommended_action == "wait_reconciliation_or_recheck_bank"
        assert "force-success" in result.bottleneck.explanation

    def test_bank_status_empty_string(self):
        eb = EvidenceBundle(
            transaction=Transaction(
                transaction_id="TXN021",
                user_id="U001",
                service_type="topup",
                amount=300_000,
                status="pending",
            ),
            reconciliation_status=ReconciliationStatus(
                transaction_id="TXN021",
                bank_status="",
            ),
        )
        result = diagnose("wallet_topup", "", eb)

        assert result.bottleneck.location == "bank"


class TestWalletTopupRule4BankFailed:
    """Rule 4: bank=failed / money=false → bank side failed."""

    def test_bank_failed(self):
        eb = EvidenceBundle(
            transaction=Transaction(
                transaction_id="TXN030",
                user_id="U001",
                service_type="topup",
                amount=500_000,
                status="pending",
            ),
            reconciliation_status=ReconciliationStatus(
                transaction_id="TXN030",
                bank_status="failed",
                money_received_in_master_wallet=False,
            ),
        )
        result = diagnose("wallet_topup", "", eb)

        assert result.bottleneck.location == "bank"
        assert result.bottleneck.confidence == "high"
        assert result.resolution.recommended_action == "wait_bank_refund_or_inform_customer"
        assert "force-success" in result.bottleneck.explanation

    def test_money_not_received(self):
        eb = EvidenceBundle(
            transaction=Transaction(
                transaction_id="TXN031",
                user_id="U001",
                service_type="topup",
                amount=500_000,
                status="pending",
            ),
            reconciliation_status=ReconciliationStatus(
                transaction_id="TXN031",
                bank_status="success",
                money_received_in_master_wallet=False,
            ),
        )
        result = diagnose("wallet_topup", "", eb)

        # bank=success but money_received=False → rule 4 (bank side)
        assert result.bottleneck.location == "bank"


class TestWalletTopupRule5MissingTransaction:
    """Rule 5: no transaction → request_more_info."""

    def test_no_transaction(self):
        eb = EvidenceBundle()  # empty evidence
        result = diagnose("wallet_topup", "", eb)

        assert result.bottleneck.location == "unknown"
        assert result.resolution.recommended_action == "request_more_info"
        assert "transaction_id" in result.missing_data

    def test_no_transaction_no_recon(self):
        eb = EvidenceBundle()
        result = diagnose("wallet_topup", "", eb)

        assert "transaction_id" in result.missing_data
        assert "bank_ref_id" in result.missing_data


class TestWalletTopupMissingReconciliation:
    """Edge case: transaction exists but no reconciliation."""

    def test_no_recon(self):
        eb = EvidenceBundle(
            transaction=Transaction(
                transaction_id="TXN040",
                user_id="U001",
                service_type="topup",
                amount=500_000,
                status="pending",
            ),
        )
        result = diagnose("wallet_topup", "", eb)

        assert result.bottleneck.location == "reconciliation"
        assert "bank_reconciliation" in result.missing_data


# ═══════════════════════════════════════════════════════════════
# FRAUD ACCOUNT LOCK SCENARIOS
# ═══════════════════════════════════════════════════════════════


class TestFraudRule1HighRiskConfirmed:
    """Rule 1: risk high + fraud confirmed → keep_locked."""

    def test_high_risk(self):
        eb = EvidenceBundle(
            account_status=AccountStatus(
                user_id="U100",
                account_status="locked",
                withdrawal_enabled=False,
                lock_reason="fraud_detection",
            ),
            fraud_case=FraudCase(
                fraud_case_id="FC001",
                user_id="U100",
                risk_score=85,
                risk_level="high",
                fraud_status="confirmed",
                recommended_decision="keep_locked",
            ),
        )
        result = diagnose("fraud_account_lock", "high_risk_lock", eb)

        assert result.bottleneck.location == "fraud_system"
        assert result.bottleneck.confidence == "high"
        assert result.resolution.recommended_action == "keep_locked"
        assert result.resolution.approval_required is True
        assert any("risk_score=85" in e for e in result.bottleneck.evidence)

    def test_high_risk_score_without_explicit_level(self):
        """High risk_score (>=70) alone should trigger keep_locked."""
        eb = EvidenceBundle(
            account_status=AccountStatus(
                user_id="U101",
                account_status="locked",
            ),
            fraud_case=FraudCase(
                fraud_case_id="FC002",
                user_id="U101",
                risk_score=75,
                risk_level=None,
                fraud_status="investigating",
            ),
        )
        result = diagnose("fraud_account_lock", "", eb)

        assert result.resolution.recommended_action == "keep_locked"


class TestFraudRule2FalsePositive:
    """Rule 2: locked + risk low → unlock."""

    def test_false_positive(self):
        eb = EvidenceBundle(
            account_status=AccountStatus(
                user_id="U200",
                account_status="locked",
                withdrawal_enabled=False,
                lock_reason="fraud_detection",
            ),
            fraud_case=FraudCase(
                fraud_case_id="FC010",
                user_id="U200",
                risk_score=20,
                risk_level="low",
                fraud_status="review_needed",
                recommended_decision="false_positive_candidate",
            ),
        )
        result = diagnose("fraud_account_lock", "", eb)

        assert result.bottleneck.location == "fraud_system"
        assert result.bottleneck.confidence == "high"
        assert result.resolution.recommended_action == "unlock_account"
        assert result.resolution.approval_required is True
        assert "false positive" in result.bottleneck.explanation

    def test_medium_risk_with_unlock_recommendation(self):
        eb = EvidenceBundle(
            account_status=AccountStatus(
                user_id="U201",
                account_status="locked",
            ),
            fraud_case=FraudCase(
                fraud_case_id="FC011",
                user_id="U201",
                risk_score=40,
                risk_level="medium",
                fraud_status="review_needed",
                recommended_decision="review_needed",
            ),
        )
        result = diagnose("fraud_account_lock", "", eb)

        assert result.resolution.recommended_action == "unlock_account"


class TestFraudRule3InsufficientEvidence:
    """Rule 3: insufficient evidence → request_more_info."""

    def test_no_fraud_case(self):
        eb = EvidenceBundle(
            account_status=AccountStatus(
                user_id="U300",
                account_status="locked",
            ),
            # fraud_case is None
        )
        result = diagnose("fraud_account_lock", "", eb)

        assert result.bottleneck.location == "fraud_system"
        assert result.bottleneck.confidence == "medium"
        assert result.resolution.recommended_action == "request_more_info"
        assert "fraud_case" in result.missing_data


class TestFraudIdentityNotFound:
    """Identity not found → request_identity_correction."""

    def test_identity_not_found(self):
        eb = EvidenceBundle()  # no account_status, no fraud_case
        result = diagnose(
            "fraud_account_lock",
            "",
            eb,
            extracted_info={"phone": "0901234567"},
        )

        assert result.bottleneck.location == "identity_lookup"
        assert result.bottleneck.confidence == "low"
        assert result.resolution.recommended_action == "request_identity_correction"
        assert "0901234567" in result.bottleneck.explanation
        assert "user_id" in result.missing_data


# ═══════════════════════════════════════════════════════════════
# GENERIC WORKFLOW
# ═══════════════════════════════════════════════════════════════


class TestGenericWorkflow:
    """Generic diagnostic for unsupported workflows."""

    def test_generic_with_transaction(self):
        eb = EvidenceBundle(
            transaction=Transaction(
                transaction_id="TXN100",
                user_id="U500",
                service_type="train_ticket",
                amount=100_000,
                status="completed",
            ),
        )
        result = diagnose("train_ticket", "provider_not_confirmed", eb)

        assert result.bottleneck.location == "unknown"
        assert result.resolution.recommended_action == "manual_review"
        assert any("transaction.status=completed" in e for e in result.bottleneck.evidence)

    def test_generic_empty_evidence(self):
        eb = EvidenceBundle()
        result = diagnose("unknown_workflow", "", eb)

        assert "transaction" in result.missing_data
        assert "wallet_ledger" in result.missing_data


# ═══════════════════════════════════════════════════════════════
# EVIDENCE DATA POINTS
# ═══════════════════════════════════════════════════════════════


class TestEvidenceDataPoints:
    """Verify evidence list contains exact data points."""

    def test_topup_evidence_includes_all_fields(self):
        eb = EvidenceBundle(
            transaction=Transaction(
                transaction_id="TXN_EP",
                user_id="U_EP",
                service_type="topup",
                amount=500_000,
                status="pending",
            ),
            reconciliation_status=ReconciliationStatus(
                transaction_id="TXN_EP",
                bank_status="success",
                money_received_in_master_wallet=True,
            ),
        )
        result = diagnose("wallet_topup", "", eb)
        evidence = result.bottleneck.evidence

        # Must contain exact data point strings
        assert any("transaction.status=pending" in e for e in evidence)
        assert any("transaction.amount=500,000đ" in e for e in evidence)
        assert any("transaction.type=topup" in e for e in evidence)
        assert any("bank_reconciliation.bank_status=success" in e for e in evidence)
        assert any("money_received_in_master_wallet=True" in e for e in evidence)

    def test_fraud_evidence_includes_signals(self):
        eb = EvidenceBundle(
            account_status=AccountStatus(
                user_id="U_EP2",
                account_status="locked",
                withdrawal_enabled=False,
            ),
            fraud_case=FraudCase(
                fraud_case_id="FC_EP",
                user_id="U_EP2",
                risk_score=85,
                risk_level="high",
                fraud_status="confirmed",
                signals={
                    "suspicious_login": True,
                    "abnormal_transaction": True,
                    "promotion_abuse": False,
                },
                device_events=[{"type": "new_device"}],
                recent_transactions=[{"amount": 10_000_000}],
            ),
        )
        result = diagnose("fraud_account_lock", "", eb)
        evidence = result.bottleneck.evidence

        assert any("risk_score=85" in e for e in evidence)
        assert any("risk_level=high" in e for e in evidence)
        assert any("suspicious_login=True" in e for e in evidence)
        assert any("abnormal_transaction=True" in e for e in evidence)
        assert any("device_events=" in e for e in evidence)
