"""Tests for Phase 1: merchant_settlement_delay extraction & routing.

Validates:
  - Mock extractor detects merchant settlement complaints
  - ExtractedInfo populates merchant-specific fields
  - Routing produces selected_workflow = merchant_settlement_delay
  - Missing info logic asks for merchant identity, NOT transaction_id
  - Existing workflows are NOT broken
"""

import pytest

from fintech_agent.llm.mock_extractor import mock_extract
from fintech_agent.nodes.extract_info import extract_info
from fintech_agent.nodes.workflow_router import route_workflow
from fintech_agent.schemas.enums import ActionType, IssueType, ServiceType


# ══════════════════════════════════════════════════════════════
# Test 1: Full merchant complaint with MRC_id
# ══════════════════════════════════════════════════════════════

class TestMerchantExtractionWithId:
    """Merchant complaint WITH merchant_id → should extract everything."""

    COMPLAINT = (
        "Tôi là merchant MRC_001_BATCH_FAIL. "
        "Đã quá chu kỳ thanh toán D+1 mà tôi chưa nhận được tiền giải ngân."
    )

    def test_service_type(self):
        info = mock_extract(self.COMPLAINT)
        assert info.service_type == "merchant_settlement"

    def test_merchant_id(self):
        info = mock_extract(self.COMPLAINT)
        assert info.merchant_id == "MRC_001_BATCH_FAIL"

    def test_settlement_cycle(self):
        info = mock_extract(self.COMPLAINT)
        assert info.settlement_cycle == "D+1"

    def test_issue_type(self):
        info = mock_extract(self.COMPLAINT)
        assert info.issue_type in ("payout_not_received", "settlement_delayed")

    def test_no_transaction_id_required(self):
        info = mock_extract(self.COMPLAINT)
        assert "transaction_id" not in info.missing_fields

    def test_no_user_id_required(self):
        info = mock_extract(self.COMPLAINT)
        assert "user_id" not in info.missing_fields

    def test_merchant_id_not_missing(self):
        info = mock_extract(self.COMPLAINT)
        assert "merchant_id" not in info.missing_fields

    def test_routing_via_extract_info(self):
        state = {
            "raw_complaint": self.COMPLAINT,
            "case_id": "CASE-MERCHANT-001",
        }
        result = extract_info(state)
        assert result["selected_workflow"] == "merchant_settlement_delay"


# ══════════════════════════════════════════════════════════════
# Test 2: Generic settlement complaint (no merchant_id)
# ══════════════════════════════════════════════════════════════

class TestMerchantExtractionWithoutId:
    """Merchant complaint WITHOUT merchant_id → should ask for identity."""

    COMPLAINT = (
        "Đã quá chu kỳ thanh toán D+1 mà tôi chưa nhận được tiền "
        "giải ngân vào tài khoản ngân hàng."
    )

    def test_service_type(self):
        info = mock_extract(self.COMPLAINT)
        assert info.service_type == "merchant_settlement"

    def test_merchant_id_missing_in_fields(self):
        info = mock_extract(self.COMPLAINT)
        assert "merchant_id" in info.missing_fields

    def test_no_transaction_id_required(self):
        info = mock_extract(self.COMPLAINT)
        assert "transaction_id" not in info.missing_fields

    def test_settlement_cycle(self):
        info = mock_extract(self.COMPLAINT)
        assert info.settlement_cycle == "D+1"

    def test_routing_still_works(self):
        state = {
            "raw_complaint": self.COMPLAINT,
            "case_id": "CASE-MERCHANT-002",
        }
        result = extract_info(state)
        assert result["selected_workflow"] == "merchant_settlement_delay"

    def test_missing_info_status(self):
        """Should go to MISSING_INFO when merchant_id is not provided."""
        state = {
            "raw_complaint": self.COMPLAINT,
            "case_id": "CASE-MERCHANT-002",
        }
        result = extract_info(state)
        assert result["status"].value == "missing_info"


# ══════════════════════════════════════════════════════════════
# Test 3: Existing workflows NOT broken
# ══════════════════════════════════════════════════════════════

class TestExistingWorkflowsUnchanged:
    """Verify existing workflows still route correctly."""

    def test_train_ticket(self):
        info = mock_extract("Tôi mua vé tàu TXN_TRAIN_001 nhưng chưa nhận được vé.")
        assert info.service_type == "train_ticket"

    def test_utility_bill(self):
        info = mock_extract("Tôi đã thanh toán tiền điện TXN_BILL_001 nhưng chưa xác nhận.")
        assert info.service_type == "electric_bill"

    def test_wallet_topup(self):
        info = mock_extract("Tôi nạp tiền TXN_TOPUP_001 nhưng ví vẫn 0đ.")
        assert info.service_type == "wallet_topup"

    def test_fraud_account_lock(self):
        info = mock_extract("Tài khoản bị khóa vô cớ, không thể rút tiền.")
        assert info.service_type == "account_security"

    def test_train_routing(self):
        state = {
            "raw_complaint": "Tôi mua vé tàu TXN_TRAIN_001 nhưng chưa nhận.",
            "case_id": "CASE-TRAIN-001",
        }
        result = extract_info(state)
        assert result["selected_workflow"] == "train_ticket"

    def test_fraud_routing(self):
        state = {
            "raw_complaint": "Tài khoản bị khóa vô cớ.",
            "case_id": "CASE-FRAUD-001",
            "user_id": "U_FRAUD_001",
        }
        result = extract_info(state)
        assert result["selected_workflow"] == "fraud_account_lock"


# ══════════════════════════════════════════════════════════════
# Test 4: Workflow router accepts merchant_settlement_delay
# ══════════════════════════════════════════════════════════════

class TestWorkflowRouterMerchant:
    """Verify workflow_router accepts merchant_settlement_delay."""

    def test_known_workflow(self):
        state = {
            "selected_workflow": "merchant_settlement_delay",
            "case_id": "CASE-MERCHANT-003",
        }
        result = route_workflow(state)
        assert result["status"].value == "rule_decision"

    def test_still_rejects_unknown(self):
        state = {
            "selected_workflow": "totally_unknown_workflow",
            "case_id": "CASE-UNK-001",
        }
        result = route_workflow(state)
        assert result["status"].value == "manual_review"


# ══════════════════════════════════════════════════════════════
# Test 5: English merchant complaint
# ══════════════════════════════════════════════════════════════

class TestEnglishMerchantComplaint:
    """English-language merchant settlement complaint."""

    COMPLAINT = "I am merchant MRC_005_BANK_PENDING. My D+1 settlement payout has not been received."

    def test_service_type(self):
        info = mock_extract(self.COMPLAINT)
        assert info.service_type == "merchant_settlement"

    def test_merchant_id(self):
        info = mock_extract(self.COMPLAINT)
        assert info.merchant_id == "MRC_005_BANK_PENDING"

    def test_settlement_cycle(self):
        info = mock_extract(self.COMPLAINT)
        assert info.settlement_cycle == "D+1"


# ══════════════════════════════════════════════════════════════
# Test 6: Additional merchant field extraction
# ══════════════════════════════════════════════════════════════

class TestMerchantFieldExtraction:
    """Test extraction of payout_id, batch_id, tax_code, bank_account."""

    def test_payout_id(self):
        info = mock_extract("Merchant MRC_001. Payout PAYOUT_20260601_001 chưa nhận. D+1.")
        assert info.payout_id == "PAYOUT_20260601_001"

    def test_batch_id(self):
        info = mock_extract("Merchant MRC_002_BATCH_NOT_GENERATED. Batch BATCH_20260601 chưa tạo. D+1 settlement.")
        assert info.batch_id is not None
        assert "BATCH_" in info.batch_id

    def test_tax_code(self):
        info = mock_extract("Tôi là cửa hàng, MST: 0123456789. Chưa nhận được tiền giải ngân D+1.")
        assert info.tax_code == "0123456789"
        # Tax code counts as merchant identity → no missing merchant_id
        assert "merchant_id" not in info.missing_fields

    def test_bank_account(self):
        info = mock_extract("Merchant MRC_003. STK: 12345678901. Giải ngân D+1 chưa nhận.")
        assert info.bank_account_number == "12345678901"

    def test_phone_counts_as_identity(self):
        info = mock_extract("Cửa hàng 0912345678 chưa nhận giải ngân D+1.")
        assert info.phone == "0912345678"
        assert "merchant_id" not in info.missing_fields


# ══════════════════════════════════════════════════════════════
# Test 7: Enum values exist
# ══════════════════════════════════════════════════════════════

class TestEnumValues:
    """Verify new enum values are accessible."""

    def test_service_type(self):
        assert ServiceType.MERCHANT_SETTLEMENT == "merchant_settlement"

    def test_issue_types(self):
        assert IssueType.SETTLEMENT_DELAYED == "settlement_delayed"
        assert IssueType.PAYOUT_NOT_RECEIVED == "payout_not_received"
        assert IssueType.PAYOUT_FAILED == "payout_failed"
        assert IssueType.BANK_ACCOUNT_INVALID == "bank_account_invalid"
        assert IssueType.UNC_NOT_RECEIVED == "unc_not_received"

    def test_action_types(self):
        assert ActionType.CREATE_MANUAL_PAYOUT_DRAFT == "create_manual_payout_draft"
        assert ActionType.CREATE_MERCHANT_EMAIL_DRAFT == "create_merchant_email_draft"
        assert ActionType.REQUEST_BANK_ACCOUNT_CORRECTION == "request_bank_account_correction"
        assert ActionType.SEND_UNC_EMAIL_DRAFT == "send_unc_email_draft"
        assert ActionType.MANUAL_SETTLEMENT_REVIEW == "manual_settlement_review"

    def test_existing_enums_unchanged(self):
        """Critical: existing enum values must still work."""
        assert ServiceType.TRAIN_TICKET == "train_ticket"
        assert ServiceType.ACCOUNT_SECURITY == "account_security"
        assert IssueType.ACCOUNT_LOCKED == "account_locked"
        assert ActionType.MANUAL_REVIEW == "manual_review"
        assert ActionType.CREATE_REFUND_REQUEST_DRAFT == "create_refund_request_draft"


# ══════════════════════════════════════════════════════════════
# Test 8: Issue subtype classification
# ══════════════════════════════════════════════════════════════

class TestMerchantIssueSubtype:
    """Verify correct issue subtype classification."""

    def test_payout_failed(self):
        info = mock_extract("Merchant MRC_001. Giải ngân thất bại. D+1.")
        assert info.issue_type == "payout_failed"

    def test_payout_not_received(self):
        info = mock_extract("Merchant MRC_001. Chưa nhận được tiền giải ngân. D+1.")
        assert info.issue_type == "payout_not_received"

    def test_settlement_delayed_default(self):
        info = mock_extract("Merchant MRC_001. Quá chu kỳ D+1 thanh toán.")
        assert info.issue_type == "settlement_delayed"

    def test_unc_not_received(self):
        info = mock_extract("Merchant MRC_001. Chưa nhận được biên lai UNC. D+1 settlement.")
        assert info.issue_type == "unc_not_received"
