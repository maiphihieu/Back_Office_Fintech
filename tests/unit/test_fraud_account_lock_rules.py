"""Tests for fraud/account lock rules engine + safety invariants.

Tests verify:
1. High-risk fraud → CREATE_REQUEST_DOCUMENTS_RESPONSE_DRAFT
2. False positive → CREATE_UNLOCK_ACCOUNT_DRAFT
3. Missing evidence → MANUAL_REVIEW
4. Account not locked → DRAFT_CUSTOMER_RESPONSE
5. Inconclusive (medium risk) → MANUAL_REVIEW
6. Safety: no execute_unlock_account, no account_status update, no withdrawal enable
"""

from __future__ import annotations

import pytest

from fintech_agent.rules.fraud_account_lock_rules import (
    FraudAccountLockDecision,
    decide_fraud_account_lock,
)
from fintech_agent.schemas.enums import ActionType, RiskLevel
from fintech_agent.schemas.evidence import (
    AccountStatus,
    EvidenceBundle,
    FraudCase,
)


# ═══════════════════════════════════════════════════════════════
#  Rule decision tests
# ═══════════════════════════════════════════════════════════════


class TestHighRiskFraud:
    """U_FRAUD_001: risk_score=82, suspicious signals → keep locked."""

    def _make_evidence(self):
        acct = AccountStatus(
            user_id="U_FRAUD_001",
            wallet_id="WALLET_FRAUD_001",
            account_status="locked",
            withdrawal_enabled=False,
            lock_reason="fraud_detection_auto_lock",
            current_balance=2500000,
        )
        fc = FraudCase(
            fraud_case_id="FRAUD_CASE_001",
            user_id="U_FRAUD_001",
            risk_score=82,
            risk_level="high",
            fraud_status="under_review",
            trigger_reason="multiple_suspicious_signals",
            signals={
                "multiple_new_devices": True,
                "suspicious_inbound_funds": True,
                "promotion_abuse": False,
                "velocity_anomaly": True,
                "blacklist_match": False,
            },
            recommended_decision="keep_locked_request_documents",
        )
        return acct, fc

    def test_action_is_request_documents(self):
        acct, fc = self._make_evidence()
        decision = decide_fraud_account_lock(acct, fc, EvidenceBundle(account_status=acct, fraud_case=fc))
        assert decision.action == ActionType.CREATE_REQUEST_DOCUMENTS_RESPONSE_DRAFT

    def test_approval_required(self):
        acct, fc = self._make_evidence()
        decision = decide_fraud_account_lock(acct, fc, EvidenceBundle(account_status=acct, fraud_case=fc))
        assert decision.approval_required is True

    def test_diagnosis(self):
        acct, fc = self._make_evidence()
        decision = decide_fraud_account_lock(acct, fc, EvidenceBundle(account_status=acct, fraud_case=fc))
        assert decision.diagnosis == "suspicious_activity_keep_locked_request_documents"

    def test_risk_classification(self):
        from fintech_agent.rules.risk_rules import classify_risk
        risk = classify_risk(ActionType.CREATE_REQUEST_DOCUMENTS_RESPONSE_DRAFT)
        assert risk == RiskLevel.HIGH


class TestFalsePositive:
    """U_FRAUD_002: risk_score=25, no suspicious signals → unlock draft."""

    def _make_evidence(self):
        acct = AccountStatus(
            user_id="U_FRAUD_002",
            wallet_id="WALLET_FRAUD_002",
            account_status="locked",
            withdrawal_enabled=False,
            lock_reason="fraud_detection_auto_lock",
            current_balance=800000,
        )
        fc = FraudCase(
            fraud_case_id="FRAUD_CASE_002",
            user_id="U_FRAUD_002",
            risk_score=25,
            risk_level="low",
            fraud_status="false_positive_candidate",
            trigger_reason="new_device_login_only",
            signals={
                "multiple_new_devices": False,
                "suspicious_inbound_funds": False,
                "promotion_abuse": False,
                "velocity_anomaly": False,
                "blacklist_match": False,
            },
            recommended_decision="unlock_account",
        )
        return acct, fc

    def test_action_is_unlock_draft(self):
        acct, fc = self._make_evidence()
        decision = decide_fraud_account_lock(acct, fc, EvidenceBundle(account_status=acct, fraud_case=fc))
        assert decision.action == ActionType.CREATE_UNLOCK_ACCOUNT_DRAFT

    def test_approval_required(self):
        acct, fc = self._make_evidence()
        decision = decide_fraud_account_lock(acct, fc, EvidenceBundle(account_status=acct, fraud_case=fc))
        assert decision.approval_required is True

    def test_diagnosis(self):
        acct, fc = self._make_evidence()
        decision = decide_fraud_account_lock(acct, fc, EvidenceBundle(account_status=acct, fraud_case=fc))
        assert decision.diagnosis == "likely_false_positive_unlock_candidate"

    def test_risk_classification(self):
        from fintech_agent.rules.risk_rules import classify_risk
        risk = classify_risk(ActionType.CREATE_UNLOCK_ACCOUNT_DRAFT)
        assert risk == RiskLevel.HIGH


class TestMissingEvidence:
    """Missing account or fraud case → manual_review."""

    def test_missing_account(self):
        fc = FraudCase(fraud_case_id="FC_001", user_id="U_001", risk_score=50)
        decision = decide_fraud_account_lock(None, fc, EvidenceBundle(fraud_case=fc))
        assert decision.action == ActionType.MANUAL_REVIEW
        assert decision.diagnosis == "missing_account_or_fraud_evidence"

    def test_missing_fraud_case(self):
        acct = AccountStatus(user_id="U_001", account_status="locked")
        decision = decide_fraud_account_lock(acct, None, EvidenceBundle(account_status=acct))
        assert decision.action == ActionType.MANUAL_REVIEW
        assert decision.diagnosis == "missing_account_or_fraud_evidence"

    def test_both_missing(self):
        decision = decide_fraud_account_lock(None, None, EvidenceBundle())
        assert decision.action == ActionType.MANUAL_REVIEW


class TestAccountNotLocked:
    """Account is active → customer response, no action needed."""

    def test_active_account(self):
        acct = AccountStatus(user_id="U_001", account_status="active")
        fc = FraudCase(fraud_case_id="FC_001", user_id="U_001", risk_score=10)
        decision = decide_fraud_account_lock(acct, fc, EvidenceBundle(account_status=acct, fraud_case=fc))
        assert decision.action == ActionType.DRAFT_CUSTOMER_RESPONSE
        assert decision.diagnosis == "account_not_locked"
        assert decision.approval_required is False


class TestInconclusive:
    """Medium risk score (50-69), no strong signals → manual_review."""

    def test_medium_risk_fallback(self):
        acct = AccountStatus(user_id="U_001", account_status="locked")
        fc = FraudCase(
            fraud_case_id="FC_001",
            user_id="U_001",
            risk_score=55,
            signals={
                "multiple_new_devices": False,
                "suspicious_inbound_funds": False,
                "promotion_abuse": False,
                "velocity_anomaly": False,
                "blacklist_match": False,
            },
        )
        decision = decide_fraud_account_lock(acct, fc, EvidenceBundle(account_status=acct, fraud_case=fc))
        assert decision.action == ActionType.MANUAL_REVIEW
        assert decision.diagnosis == "fraud_review_inconclusive"
        assert decision.approval_required is True


class TestConflictDetected:
    """Conflicts in evidence → always manual_review."""

    def test_conflict_overrides_fraud_decision(self):
        from fintech_agent.schemas.evidence import EvidenceConflict
        acct = AccountStatus(user_id="U_001", account_status="locked")
        fc = FraudCase(fraud_case_id="FC_001", user_id="U_001", risk_score=25)
        bundle = EvidenceBundle(
            account_status=acct,
            fraud_case=fc,
            conflicts=[EvidenceConflict(
                source_a="account", source_b="fraud_case",
                field="user_id", value_a="U_001", value_b="U_002",
                description="user_id mismatch",
            )],
        )
        decision = decide_fraud_account_lock(acct, fc, bundle)
        assert decision.action == ActionType.MANUAL_REVIEW
        assert decision.diagnosis == "conflict_detected"


class TestBlacklistTrigger:
    """Even low risk_score, blacklist_match → fraud likely."""

    def test_blacklist_match_overrides_score(self):
        acct = AccountStatus(user_id="U_001", account_status="locked")
        fc = FraudCase(
            fraud_case_id="FC_001",
            user_id="U_001",
            risk_score=30,
            signals={"blacklist_match": True},
        )
        decision = decide_fraud_account_lock(acct, fc, EvidenceBundle(account_status=acct, fraud_case=fc))
        assert decision.action == ActionType.CREATE_REQUEST_DOCUMENTS_RESPONSE_DRAFT


# ═══════════════════════════════════════════════════════════════
#  Safety invariant tests
# ═══════════════════════════════════════════════════════════════


class TestSafetyInvariants:
    """Verify safety-by-design: no execute actions, no account modifications."""

    def test_no_execute_unlock_in_forbidden_actions(self):
        from fintech_agent.safety.money_action_guard import FORBIDDEN_ACTIONS
        assert "execute_unlock_account" in FORBIDDEN_ACTIONS
        assert "modify_account_status" in FORBIDDEN_ACTIONS

    def test_unlock_draft_is_not_forbidden(self):
        """Draft creation should NOT be blocked by safety guard."""
        from fintech_agent.safety.money_action_guard import is_safe_action
        assert is_safe_action("create_unlock_account_draft") is True
        assert is_safe_action("create_request_documents_response_draft") is True

    def test_execute_actions_are_forbidden(self):
        from fintech_agent.safety.money_action_guard import is_safe_action
        assert is_safe_action("execute_unlock_account") is False
        assert is_safe_action("modify_account_status") is False

    def test_no_account_status_write_in_handlers(self):
        """Verify handlers don't contain account modification code.

        We check for actual Python statements that would modify account,
        not docstring text that describes what the handler does NOT do.
        """
        import inspect
        from fintech_agent.mcp_server.handlers import (
            handle_create_unlock_account_draft,
            handle_create_request_documents_response_draft,
        )
        unlock_src = inspect.getsource(handle_create_unlock_account_draft)
        request_docs_src = inspect.getsource(handle_create_request_documents_response_draft)
        combined = unlock_src + request_docs_src

        # Should not contain repo update calls
        assert ".update(" not in combined, "Handler should not update any repo"
        assert ".upsert(" not in combined, "Handler should not upsert any repo"
        assert "repo.unlock" not in combined.lower(), "Handler should not unlock accounts"
        assert "withdrawal_enabled=True" not in combined, "Handler should not enable withdrawals"

    def test_decision_is_frozen_dataclass(self):
        """FraudAccountLockDecision should be immutable."""
        decision = FraudAccountLockDecision(
            action=ActionType.MANUAL_REVIEW,
            diagnosis="test",
            approval_required=True,
        )
        with pytest.raises(Exception):
            decision.action = ActionType.NO_ACTION  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════
#  Mock extractor tests
# ═══════════════════════════════════════════════════════════════


class TestMockExtractorFraud:
    """Verify mock extractor detects fraud/account lock complaints."""

    def test_locked_account_complaint(self):
        from fintech_agent.llm.mock_extractor import mock_extract
        result = mock_extract("Tài khoản của tôi bất ngờ bị khóa vô cớ, tôi không thể rút tiền.")
        assert result.service_type == "account_security"
        assert result.issue_type == "account_locked"

    def test_extracts_fraud_user_id(self):
        from fintech_agent.llm.mock_extractor import mock_extract
        result = mock_extract("Tài khoản U_FRAUD_002 bị khóa, tôi không thể rút tiền")
        assert result.user_id == "U_FRAUD_002"

    def test_no_default_fraud_user_id(self):
        """Without user_id or phone/email/wallet_id, user_id must be None.

        The old behavior defaulted to U_FRAUD_001 which is UNSAFE —
        it would fetch the wrong user's data. Identity resolution now
        happens in fetch_evidence via phone/email/wallet_id lookup.
        """
        from fintech_agent.llm.mock_extractor import mock_extract
        result = mock_extract("Tài khoản bị khóa vô cớ, không rút được tiền")
        assert result.user_id is None
        assert "user_id" in result.missing_fields

    def test_no_transaction_id_required(self):
        from fintech_agent.llm.mock_extractor import mock_extract
        result = mock_extract("Tài khoản bị khóa vô cớ")
        assert "transaction_id" not in result.missing_fields


# ═══════════════════════════════════════════════════════════════
#  Risk rules tests
# ═══════════════════════════════════════════════════════════════


class TestRiskRulesFraud:
    """Verify risk classification for fraud action types."""

    def test_unlock_draft_is_high(self):
        from fintech_agent.rules.risk_rules import classify_risk, requires_approval
        assert classify_risk(ActionType.CREATE_UNLOCK_ACCOUNT_DRAFT) == RiskLevel.HIGH
        assert requires_approval(ActionType.CREATE_UNLOCK_ACCOUNT_DRAFT) is True

    def test_request_docs_is_high(self):
        from fintech_agent.rules.risk_rules import classify_risk, requires_approval
        assert classify_risk(ActionType.CREATE_REQUEST_DOCUMENTS_RESPONSE_DRAFT) == RiskLevel.HIGH
        assert requires_approval(ActionType.CREATE_REQUEST_DOCUMENTS_RESPONSE_DRAFT) is True
