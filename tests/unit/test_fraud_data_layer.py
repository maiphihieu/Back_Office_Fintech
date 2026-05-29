"""Tests for fraud/account lock data layer (use case 2).

These tests verify:
1. Schema models (AccountStatus, FraudCase) work correctly
2. EvidenceBundle extended fields are backward compatible
3. Supabase repositories can be instantiated and called (live DB)
4. Repository factory returns the correct repo types
"""

from __future__ import annotations

import pytest

from fintech_agent.schemas.evidence import (
    AccountStatus,
    EvidenceBundle,
    FraudCase,
)


# ═══════════════════════════════════════════════════════════════
#  Schema / Model tests (no DB required)
# ═══════════════════════════════════════════════════════════════


class TestAccountStatusModel:
    """Verify AccountStatus model parsing."""

    def test_basic_construction(self):
        acct = AccountStatus(
            user_id="U_FRAUD_001",
            wallet_id="WALLET_001",
            account_status="locked",
            withdrawal_enabled=False,
            lock_reason="fraud_detection_auto_lock",
            current_balance=2500000,
        )
        assert acct.user_id == "U_FRAUD_001"
        assert acct.account_status == "locked"
        assert acct.withdrawal_enabled is False
        assert acct.current_balance == 2500000

    def test_optional_fields_default_none(self):
        acct = AccountStatus(user_id="U_TEST")
        assert acct.wallet_id is None
        assert acct.account_status is None
        assert acct.locked_at is None

    def test_empty_user_id_rejected(self):
        with pytest.raises(Exception):
            AccountStatus(user_id="")


class TestFraudCaseModel:
    """Verify FraudCase model parsing."""

    def test_basic_construction(self):
        fc = FraudCase(
            fraud_case_id="FRAUD_CASE_001",
            user_id="U_FRAUD_001",
            risk_score=82,
            risk_level="high",
            fraud_status="under_review",
            trigger_reason="multiple_suspicious_signals",
            signals={"velocity_anomaly": True},
            recommended_decision="keep_locked_request_documents",
        )
        assert fc.risk_score == 82
        assert fc.risk_level == "high"
        assert fc.signals["velocity_anomaly"] is True
        assert fc.recommended_decision == "keep_locked_request_documents"

    def test_defaults(self):
        fc = FraudCase(fraud_case_id="FC_001", user_id="U_001")
        assert fc.signals == {}
        assert fc.recent_transactions == []
        assert fc.device_events == []
        assert fc.recommended_decision is None

    def test_low_risk_false_positive(self):
        fc = FraudCase(
            fraud_case_id="FRAUD_CASE_002",
            user_id="U_FRAUD_002",
            risk_score=25,
            risk_level="low",
            fraud_status="false_positive_candidate",
            recommended_decision="unlock_account",
        )
        assert fc.risk_score == 25
        assert fc.recommended_decision == "unlock_account"


class TestEvidenceBundleBackwardCompat:
    """Ensure new fields don't break existing EvidenceBundle usage."""

    def test_empty_bundle_still_works(self):
        eb = EvidenceBundle()
        assert eb.account_status is None
        assert eb.fraud_case is None
        assert eb.transaction is None
        assert eb.has_conflicts is False

    def test_bundle_with_fraud_fields(self):
        acct = AccountStatus(user_id="U_001", account_status="locked")
        fc = FraudCase(fraud_case_id="FC_001", user_id="U_001", risk_score=90)
        eb = EvidenceBundle(account_status=acct, fraud_case=fc)
        assert eb.account_status.account_status == "locked"
        assert eb.fraud_case.risk_score == 90
        # Existing fields still default to None
        assert eb.transaction is None
        assert eb.wallet_ledger is None

    def test_serialization_round_trip(self):
        acct = AccountStatus(user_id="U_001", account_status="active")
        fc = FraudCase(
            fraud_case_id="FC_001",
            user_id="U_001",
            signals={"a": True},
        )
        eb = EvidenceBundle(account_status=acct, fraud_case=fc)
        data = eb.model_dump(mode="json")
        eb2 = EvidenceBundle.model_validate(data)
        assert eb2.account_status.user_id == "U_001"
        assert eb2.fraud_case.signals == {"a": True}


# ═══════════════════════════════════════════════════════════════
#  Repository factory tests (no DB required for import check)
# ═══════════════════════════════════════════════════════════════


class TestRepositoryFactory:
    """Verify factory functions return correct repo types."""

    def test_get_account_repo_returns_supabase(self):
        from fintech_agent.database.repository_factory import get_account_repo
        from fintech_agent.repositories.supabase.supabase_account_repo import (
            SupabaseAccountRepository,
        )
        repo = get_account_repo()
        assert isinstance(repo, SupabaseAccountRepository)

    def test_get_fraud_repo_returns_supabase(self):
        from fintech_agent.database.repository_factory import get_fraud_repo
        from fintech_agent.repositories.supabase.supabase_fraud_repo import (
            SupabaseFraudRepository,
        )
        repo = get_fraud_repo()
        assert isinstance(repo, SupabaseFraudRepository)


# ═══════════════════════════════════════════════════════════════
#  Live Supabase tests (require tables + seed data)
# ═══════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    False,  # Migration 002 + seed 003 applied in Supabase
    reason="Skip until migration 002 + seed 003 are applied in Supabase Dashboard",
)
class TestLiveSupabaseData:
    """Verify seeded data can be retrieved via repositories."""

    def test_get_account_fraud_001(self):
        from fintech_agent.database.repository_factory import get_account_repo
        repo = get_account_repo()
        acct = repo.get_account_status("U_FRAUD_001")
        assert acct is not None
        assert acct.account_status == "locked"
        assert acct.withdrawal_enabled is False
        assert acct.current_balance == 2500000

    def test_get_fraud_case_high_risk(self):
        from fintech_agent.database.repository_factory import get_fraud_repo
        repo = get_fraud_repo()
        fc = repo.get_fraud_case("U_FRAUD_001")
        assert fc is not None
        assert fc.risk_score == 82
        assert fc.risk_level == "high"
        assert fc.signals.get("velocity_anomaly") is True

    def test_get_fraud_case_false_positive(self):
        from fintech_agent.database.repository_factory import get_fraud_repo
        repo = get_fraud_repo()
        fc = repo.get_fraud_case("U_FRAUD_002")
        assert fc is not None
        assert fc.risk_score == 25
        assert fc.risk_level == "low"
        assert fc.recommended_decision == "unlock_account"

    def test_nonexistent_user_returns_none(self):
        from fintech_agent.database.repository_factory import get_account_repo
        repo = get_account_repo()
        acct = repo.get_account_status("U_NONEXISTENT")
        assert acct is None
