"""Unit tests for workflow-aware missing-info validation.

Tests verify that:
1. fraud_account_lock does NOT require transaction_id
2. fraud_account_lock requires identity (phone/email/user_id/wallet_id)
3. Transaction-based workflows still require transaction_id
4. Missing-info handler routes correctly per workflow
5. Ticket builder uses correct expected evidence per workflow
"""

import pytest

from fintech_agent.llm.mock_extractor import mock_extract
from fintech_agent.nodes.extract_info import extract_info
from fintech_agent.nodes.missing_info import missing_info_handler
from fintech_agent.schemas.enums import CaseStatus


# ═══════════════════════════════════════════════════════════
#  Test 1: Fraud complaint without identity → asks for identity, NOT txn
# ═══════════════════════════════════════════════════════════

class TestFraudMissingIdentity:

    def test_fraud_complaint_no_identity_no_txn_required(self) -> None:
        """Fraud complaint with no identity should ask for user_id,
        NOT for transaction_id."""
        result = extract_info({
            "raw_complaint": "Tài khoản của tôi bất ngờ bị khóa vô cớ, tôi không thể rút tiền.",
        })
        assert result["selected_workflow"] == "fraud_account_lock"
        assert "transaction_id" not in result["missing_info"]
        assert "user_id" in result["missing_info"]

    def test_fraud_missing_info_dead_letter_identity(self) -> None:
        """Missing-info handler for fraud should dead-letter with identity message,
        not transaction message."""
        result = missing_info_handler({
            "missing_info": ["user_id"],
            "selected_workflow": "fraud_account_lock",
            "errors": [],
        })
        assert result["status"] == CaseStatus.DEAD_LETTER
        errors_str = str(result["errors"])
        assert "identity_missing" in errors_str
        assert "số điện thoại/email/user_id/wallet_id" in errors_str

    def test_fraud_missing_info_does_not_mention_transaction(self) -> None:
        """Fraud dead-letter should NOT mention transaction_id."""
        result = missing_info_handler({
            "missing_info": ["user_id"],
            "selected_workflow": "fraud_account_lock",
            "errors": [],
        })
        errors_str = str(result["errors"])
        assert "transaction_id missing" not in errors_str


# ═══════════════════════════════════════════════════════════
#  Test 2: Fraud complaint with phone → no missing info
# ═══════════════════════════════════════════════════════════

class TestFraudWithPhone:

    def test_fraud_phone_no_missing(self) -> None:
        """Fraud complaint with phone extracted should have NO missing info."""
        result = extract_info({
            "raw_complaint": (
                "Tài khoản của tôi bất ngờ bị khóa vô cớ, "
                "tôi không thể rút tiền. Số điện thoại 0981000001"
            ),
        })
        assert result["selected_workflow"] == "fraud_account_lock"
        assert result["missing_info"] == []
        assert result["status"] == CaseStatus.FETCHING_EVIDENCE
        assert result["extracted_info"].phone == "0981000001"

    def test_fraud_phone_not_ask_for_txn(self) -> None:
        """Fraud with phone: should never ask for transaction_id."""
        result = extract_info({
            "raw_complaint": (
                "Tài khoản bị khóa vô cớ. "
                "Số điện thoại 0981000002"
            ),
        })
        assert "transaction_id" not in result["missing_info"]


# ═══════════════════════════════════════════════════════════
#  Test 3: Transaction workflows still require transaction_id
# ═══════════════════════════════════════════════════════════

class TestTransactionWorkflowsStillNeedTxn:

    def test_train_ticket_requires_txn_id(self) -> None:
        """Train ticket complaint without txn_id → missing transaction_id."""
        result = extract_info({
            "raw_complaint": "Tôi mua vé tàu nhưng chưa nhận được vé",
            "user_id": "U001",
        })
        assert "transaction_id" in result["missing_info"]

    def test_wallet_topup_requires_txn_id(self) -> None:
        """Wallet topup complaint without txn_id → missing transaction_id."""
        result = extract_info({
            "raw_complaint": "Tôi nạp tiền nhưng ví vẫn 0đ",
            "user_id": "U001",
        })
        assert "transaction_id" in result["missing_info"]

    def test_train_missing_info_dead_letter_txn(self) -> None:
        """Train ticket with missing txn_id → dead_letter with txn message."""
        result = missing_info_handler({
            "missing_info": ["transaction_id"],
            "selected_workflow": "train_ticket",
            "errors": [],
        })
        assert result["status"] == CaseStatus.DEAD_LETTER
        assert any("transaction_id missing" in e for e in result["errors"])

    def test_utility_missing_info_dead_letter_txn(self) -> None:
        """Utility bill with missing txn_id → dead_letter."""
        result = missing_info_handler({
            "missing_info": ["transaction_id"],
            "selected_workflow": "utility_bill",
            "errors": [],
        })
        assert result["status"] == CaseStatus.DEAD_LETTER

    def test_wallet_topup_missing_info_dead_letter_txn(self) -> None:
        """Wallet topup with missing txn_id → dead_letter."""
        result = missing_info_handler({
            "missing_info": ["transaction_id"],
            "selected_workflow": "wallet_topup",
            "errors": [],
        })
        assert result["status"] == CaseStatus.DEAD_LETTER


# ═══════════════════════════════════════════════════════════
#  Test 4: Fraud with txn_id in missing_info does NOT dead-letter
# ═══════════════════════════════════════════════════════════

class TestFraudDoesNotDeadLetterOnTxn:

    def test_fraud_txn_missing_proceeds(self) -> None:
        """Fraud workflow: missing transaction_id should NOT cause dead-letter."""
        result = missing_info_handler({
            "missing_info": ["transaction_id"],
            "selected_workflow": "fraud_account_lock",
            "errors": [],
        })
        # Should proceed (not dead_letter)
        assert result["status"] == CaseStatus.FETCHING_EVIDENCE

    def test_fraud_no_missing_proceeds(self) -> None:
        """Fraud workflow with no missing info → proceeds normally."""
        result = missing_info_handler({
            "missing_info": [],
            "selected_workflow": "fraud_account_lock",
            "errors": [],
        })
        assert result["status"] == CaseStatus.FETCHING_EVIDENCE


# ═══════════════════════════════════════════════════════════
#  Test 5: No U_FRAUD_001 fallback anywhere
# ═══════════════════════════════════════════════════════════

class TestNoFraudFallback:

    def test_no_fallback_user_id(self) -> None:
        """Fraud complaint should never default to U_FRAUD_001."""
        result = mock_extract(
            "Tài khoản bị khóa vô cớ. Số điện thoại 0981000001"
        )
        assert result.user_id != "U_FRAUD_001"

    def test_no_fallback_missing_all(self) -> None:
        """Even with no identity at all, should not default to U_FRAUD_001."""
        result = mock_extract(
            "Tài khoản của tôi bị khóa vô cớ"
        )
        assert result.user_id is None or result.user_id != "U_FRAUD_001"


# ═══════════════════════════════════════════════════════════
#  Test 6: Ticket builder evidence expectations
# ═══════════════════════════════════════════════════════════

class TestTicketBuilderWorkflowEvidence:

    def test_fraud_expects_account_fraud_evidence(self) -> None:
        """Fraud ticket builder should expect account_status + fraud_case,
        NOT transaction + wallet_ledger."""
        from fintech_agent.llm.ticket_builder import (
            _compute_evidence_checked_and_missing,
        )
        from fintech_agent.schemas.evidence import EvidenceBundle

        # Empty evidence for fraud workflow
        _, missing = _compute_evidence_checked_and_missing({
            "evidence_bundle": EvidenceBundle(),
            "selected_workflow": "fraud_account_lock",
        })
        # Should NOT mention transaction or wallet_ledger as missing
        assert "Dữ liệu giao dịch" not in missing
        assert "Sổ cái ví" not in missing
        # Should mention account_status and fraud_case as missing
        assert "Trạng thái tài khoản" in missing
        assert "Dữ liệu fraud/risk" in missing

    def test_train_expects_transaction_evidence(self) -> None:
        """Train ticket builder should expect transaction + wallet_ledger."""
        from fintech_agent.llm.ticket_builder import (
            _compute_evidence_checked_and_missing,
        )
        from fintech_agent.schemas.evidence import EvidenceBundle

        _, missing = _compute_evidence_checked_and_missing({
            "evidence_bundle": EvidenceBundle(),
            "selected_workflow": "train_ticket",
        })
        assert "Dữ liệu giao dịch" in missing
        assert "Sổ cái ví" in missing
        # Should NOT mention fraud evidence as missing
        assert "Trạng thái tài khoản" not in missing
        assert "Dữ liệu fraud/risk" not in missing


# ═══════════════════════════════════════════════════════════
#  Test 7: End-to-end extract_info routing
# ═══════════════════════════════════════════════════════════

class TestExtractInfoRouting:

    def test_fraud_routes_correctly(self) -> None:
        """Fraud complaint routes to fraud_account_lock workflow."""
        result = extract_info({
            "raw_complaint": (
                "Tài khoản của tôi bị khóa vô cớ, không thể rút tiền. "
                "Số điện thoại 0981000001"
            ),
        })
        assert result["selected_workflow"] == "fraud_account_lock"
        assert result["extracted_info"].service_type == "account_security"
        assert result["extracted_info"].phone == "0981000001"
        assert result["status"] == CaseStatus.FETCHING_EVIDENCE

    def test_train_routes_correctly(self) -> None:
        """Train complaint with txn routes to train_ticket."""
        result = extract_info({
            "raw_complaint": "Mua vé tàu TXN_TRAIN_001 nhưng chưa nhận",
            "user_id": "U001",
        })
        assert result["selected_workflow"] == "train_ticket"
        assert result["status"] == CaseStatus.FETCHING_EVIDENCE
