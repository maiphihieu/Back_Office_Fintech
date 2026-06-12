"""Tests for fraud/account lock identity resolution (Phase 1).

Tests verify:
1. Phone number extraction from complaint text
2. Email extraction from complaint text
3. Wallet_id extraction from complaint text
4. Phone-to-user_id resolution via MCP (0981000001 → U_FRAUD_FP)
5. Missing identity → no fallback, manual_review path
6. Existing user_id-based flows still work (non-regression)
"""

from __future__ import annotations

import pytest

from fintech_agent.llm.mock_extractor import mock_extract
from fintech_agent.schemas.enums import ActionType, CaseStatus


# ═══════════════════════════════════════════════════════════════
#  Extraction tests — phone, email, wallet_id
# ═══════════════════════════════════════════════════════════════


class TestPhoneExtraction:
    """Verify phone number extraction from Vietnamese complaint text."""

    def test_phone_with_prefix_09(self):
        result = mock_extract(
            "Tài khoản bị khóa vô cớ. Số điện thoại 0981000001"
        )
        assert result.phone == "0981000001"

    def test_phone_with_prefix_03(self):
        result = mock_extract(
            "Tài khoản bị khóa. SĐT 0371234567"
        )
        assert result.phone == "0371234567"

    def test_phone_with_prefix_07(self):
        result = mock_extract(
            "Không rút được tiền, phone: 0712345678"
        )
        assert result.phone == "0712345678"

    def test_phone_with_prefix_08(self):
        result = mock_extract(
            "Tài khoản khóa. Liên hệ 0812345678"
        )
        assert result.phone == "0812345678"

    def test_phone_with_prefix_05(self):
        result = mock_extract(
            "Tài khoản khóa, SĐT: 0561234567"
        )
        assert result.phone == "0561234567"

    def test_no_phone_in_text(self):
        result = mock_extract(
            "Tài khoản bị khóa vô cớ, tôi không thể rút tiền."
        )
        assert result.phone is None

    def test_phone_does_not_match_short_numbers(self):
        """Phone numbers must be exactly 10 digits."""
        result = mock_extract(
            "Tài khoản bị khóa. Mã 098100 không đúng."
        )
        assert result.phone is None

    def test_phone_coexists_with_user_id(self):
        """Both phone and user_id can be extracted."""
        result = mock_extract(
            "Tài khoản U_FRAUD_FP bị khóa. SĐT 0981000001"
        )
        assert result.user_id == "U_FRAUD_FP"
        assert result.phone == "0981000001"


class TestEmailExtraction:
    """Verify email extraction from complaint text."""

    def test_email_basic(self):
        result = mock_extract(
            "Tài khoản bị khóa. Email user@example.com"
        )
        assert result.email == "user@example.com"

    def test_no_email_in_text(self):
        result = mock_extract(
            "Tài khoản bị khóa vô cớ."
        )
        assert result.email is None


class TestWalletIdExtraction:
    """Verify wallet_id extraction from complaint text."""

    def test_wallet_id_extracted(self):
        result = mock_extract(
            "Tài khoản bị khóa. Ví WALLET_FRAUD_001"
        )
        assert result.wallet_id == "WALLET_FRAUD_001"

    def test_no_wallet_id_in_text(self):
        result = mock_extract(
            "Tài khoản bị khóa."
        )
        assert result.wallet_id is None


# ═══════════════════════════════════════════════════════════════
#  Fraud extractor + routing: phone-only complaint
# ═══════════════════════════════════════════════════════════════


class TestFraudPhoneOnlyExtraction:
    """Verify extraction for the primary business case:
    complaint with phone number but no user_id.
    """

    COMPLAINT = (
        "Tài khoản của tôi bất ngờ bị khóa vô cớ, "
        "tôi không thể rút tiền. Số điện thoại 0981000001"
    )

    def test_service_type(self):
        result = mock_extract(self.COMPLAINT)
        assert result.service_type == "account_security"

    def test_issue_type(self):
        result = mock_extract(self.COMPLAINT)
        assert result.issue_type == "account_locked"

    def test_phone_extracted(self):
        result = mock_extract(self.COMPLAINT)
        assert result.phone == "0981000001"

    def test_user_id_is_none(self):
        """user_id should NOT default to U_FRAUD_001."""
        result = mock_extract(self.COMPLAINT)
        assert result.user_id is None

    def test_user_id_not_in_missing_when_phone_present(self):
        """Phone can resolve user_id, so user_id is not 'missing'."""
        result = mock_extract(self.COMPLAINT)
        assert "user_id" not in result.missing_fields


# ═══════════════════════════════════════════════════════════════
#  Identity resolution via fetch_evidence (MCP integration)
# ═══════════════════════════════════════════════════════════════


class TestPhoneToUserIdResolution:
    """Test 1: Phone 0981000001 → U_FRAUD_FP via fetch_evidence."""

    COMPLAINT = (
        "Tài khoản của tôi bị khóa vô cớ, "
        "tôi không thể rút tiền. Số điện thoại 0981000001"
    )

    def test_identity_resolved_to_fraud_fp(self):
        from fintech_agent.nodes.fetch_evidence import fetch_evidence
        extracted = mock_extract(self.COMPLAINT)
        state = {
            "case_id": "CASE_PHONE_RESOLVE_001",
            "extracted_info": extracted,
        }
        result = fetch_evidence(state)

        assert result["selected_workflow"] == "fraud_account_lock"

        # Identity should resolve via phone lookup
        tool_results = result.get("tool_results", {})
        assert tool_results.get("identity_lookup") == "ok_phone", \
            f"Identity lookup did not succeed: {tool_results}"

        # Should resolve to U_FRAUD_FP
        assert result.get("user_id") == "U_FRAUD_FP", \
            f"Expected U_FRAUD_FP, got {result.get('user_id')}"

        # Evidence should be fetched for U_FRAUD_FP
        evidence = result["evidence_bundle"]
        assert evidence.account_status is not None, "account_status not fetched"
        assert evidence.fraud_case is not None, "fraud_case not fetched"


class TestPhoneToUserIdHighRisk:
    """Test 2: Phone 0981000002 → U_FRAUD_HIGH."""

    COMPLAINT = (
        "Tài khoản bị khóa, không rút được tiền. SĐT 0981000002"
    )

    def test_resolves_to_fraud_high(self):
        from fintech_agent.nodes.fetch_evidence import fetch_evidence
        extracted = mock_extract(self.COMPLAINT)
        state = {
            "case_id": "CASE_PHONE_RESOLVE_002",
            "extracted_info": extracted,
        }
        result = fetch_evidence(state)
        assert result.get("user_id") == "U_FRAUD_HIGH"


class TestPhoneToUserIdMissing:
    """Test 3: Phone 0981000003 → U_FRAUD_MISSING."""

    COMPLAINT = (
        "Tài khoản bị khóa vô cớ. Số điện thoại 0981000003"
    )

    def test_resolves_to_fraud_missing(self):
        from fintech_agent.nodes.fetch_evidence import fetch_evidence
        extracted = mock_extract(self.COMPLAINT)
        state = {
            "case_id": "CASE_PHONE_RESOLVE_003",
            "extracted_info": extracted,
        }
        result = fetch_evidence(state)
        assert result.get("user_id") == "U_FRAUD_MISSING"


class TestMissingIdentity:
    """Test 4: No user_id, no phone, no email, no wallet_id.

    Expected: no fallback, no wrong evidence, safe for manual_review.
    """

    COMPLAINT = "Tài khoản bị khóa vô cớ, tôi không thể rút tiền."

    def test_extraction_has_no_identity(self):
        result = mock_extract(self.COMPLAINT)
        assert result.user_id is None
        assert result.phone is None
        assert result.email is None
        assert result.wallet_id is None
        assert "user_id" in result.missing_fields

    def test_fetch_evidence_returns_empty(self):
        from fintech_agent.nodes.fetch_evidence import fetch_evidence
        extracted = mock_extract(self.COMPLAINT)
        state = {
            "case_id": "CASE_MISSING_IDENTITY",
            "extracted_info": extracted,
        }
        result = fetch_evidence(state)

        # Identity should be reported as missing
        tool_results = result.get("tool_results", {})
        assert tool_results.get("identity_lookup") == "missing"

        # Evidence should be empty (no wrong user data)
        evidence = result["evidence_bundle"]
        assert evidence.account_status is None
        assert evidence.fraud_case is None

        # Errors should mention identity
        assert any("identity_not_resolved" in e for e in result.get("errors", []))

    def test_rule_engine_routes_to_manual_review(self):
        """When evidence is empty (missing identity), rules → manual_review."""
        from fintech_agent.nodes.fetch_evidence import fetch_evidence
        from fintech_agent.nodes.rule_decision import apply_rules
        extracted = mock_extract(self.COMPLAINT)
        state = {
            "case_id": "CASE_MISSING_IDENTITY_RULES",
            "selected_workflow": "fraud_account_lock",
            "extracted_info": extracted,
        }
        state.update(fetch_evidence(state))
        rule_result = apply_rules(state)
        decision = rule_result["rule_decision"]
        assert decision["action"] == ActionType.MANUAL_REVIEW.value
        assert decision["diagnosis"] == "missing_account_or_fraud_evidence"


# ═══════════════════════════════════════════════════════════════
#  Non-regression: existing user_id-based flow still works
# ═══════════════════════════════════════════════════════════════


class TestExistingUserIdFlowNonRegression:
    """Test 5: User_id in complaint text still works (no regression)."""

    def test_user_id_001_high_risk(self):
        """U_FRAUD_001 explicitly in text → works as before."""
        from fintech_agent.nodes.fetch_evidence import fetch_evidence
        complaint = "Tài khoản U_FRAUD_001 bị khóa vô cớ"
        extracted = mock_extract(complaint)
        assert extracted.user_id == "U_FRAUD_001"
        state = {
            "case_id": "CASE_NONREG_001",
            "extracted_info": extracted,
        }
        result = fetch_evidence(state)
        evidence = result["evidence_bundle"]
        assert evidence.account_status is not None
        assert evidence.fraud_case is not None

    def test_user_id_002_false_positive(self):
        """U_FRAUD_002 explicitly in text → works as before."""
        from fintech_agent.nodes.fetch_evidence import fetch_evidence
        complaint = "Tài khoản U_FRAUD_002 bị khóa, tôi không thể rút tiền"
        extracted = mock_extract(complaint)
        assert extracted.user_id == "U_FRAUD_002"
        state = {
            "case_id": "CASE_NONREG_002",
            "extracted_info": extracted,
        }
        result = fetch_evidence(state)
        evidence = result["evidence_bundle"]
        assert evidence.account_status is not None

    def test_wallet_topup_not_affected(self):
        """Wallet topup extraction must not be affected."""
        complaint = (
            "Tôi nạp tiền từ ngân hàng vào ví, bank đã trừ tiền "
            "nhưng ví vẫn báo 0 đồng. Mã giao dịch TXN_TOPUP_001"
        )
        result = mock_extract(complaint)
        assert result.service_type == "wallet_topup"
        assert result.issue_type == "topup_pending"
        assert result.phone is None  # no phone in this complaint


# ═══════════════════════════════════════════════════════════════
#  MCP client has identity resolution tools
# ═══════════════════════════════════════════════════════════════


class TestMCPClientIdentityTools:
    """Verify MCP client handler map includes identity resolution tools."""

    def test_handler_map_has_identity_tools(self):
        from fintech_agent.mcp_server.server import mcp
        tool_names = mcp._tool_manager._tools.keys()
        assert "get_user_by_phone" in tool_names
        assert "get_user_by_email" in tool_names
        assert "get_user_by_wallet_id" in tool_names

    def test_existing_fraud_tools_still_present(self):
        from fintech_agent.mcp_server.server import mcp
        tool_names = mcp._tool_manager._tools.keys()
        assert "get_account_status" in tool_names
        assert "get_fraud_case" in tool_names
        assert "create_unlock_account_draft" in tool_names
        assert "create_request_documents_response_draft" in tool_names


# ═══════════════════════════════════════════════════════════════
#  Safety: identity resolution tools are read-only
# ═══════════════════════════════════════════════════════════════


class TestIdentityToolsSafety:
    """Verify identity resolution handlers don't modify data."""

    def test_handlers_are_read_only(self):
        """Scan handler source for write operations."""
        import inspect
        from fintech_agent.mcp_server.handlers import (
            handle_get_user_by_phone,
            handle_get_user_by_email,
            handle_get_user_by_wallet_id,
        )
        for handler in [
            handle_get_user_by_phone,
            handle_get_user_by_email,
            handle_get_user_by_wallet_id,
        ]:
            source = inspect.getsource(handler)
            assert ".update(" not in source, \
                f"{handler.__name__} contains .update() — must be read-only"
            assert ".upsert(" not in source, \
                f"{handler.__name__} contains .upsert() — must be read-only"
            assert ".insert(" not in source, \
                f"{handler.__name__} contains .insert() — must be read-only"
            assert ".delete(" not in source, \
                f"{handler.__name__} contains .delete() — must be read-only"
