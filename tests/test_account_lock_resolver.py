"""Account-lock: the agent looks up the logged-in account's real lock status
and concludes (locked / active) instead of looping for screenshots.

Offline/deterministic: the account repository is mocked; no Supabase, no LLM.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fintech_agent.api.generic_resolver import resolve_case_evidence
from fintech_agent.llm.message_analyzer import (
    ExtractedFields,
    MessageAnalysis,
    load_response_policy,
)
from fintech_agent.safety.evidence_mapper import to_public_safe_evidence


def _acct(status, withdrawal_enabled=False):
    return SimpleNamespace(
        user_id="U1", wallet_id="W1", account_status=status,
        withdrawal_enabled=withdrawal_enabled, lock_reason=None,
        risk_score=95, fraud_status="suspected",  # must NOT leak to the customer
    )


@pytest.fixture
def patch_account_repo(monkeypatch):
    def _install(acct):
        repo = MagicMock()
        repo.get_account_status.return_value = acct
        monkeypatch.setattr(
            "fintech_agent.database.repository_factory.get_account_repo",
            lambda *a, **k: repo,
        )
        return repo

    return _install


_SESSION = {"subject_type": "wallet_user", "user_id": "U1"}


def _fraud_complaint():
    return MessageAnalysis(
        message_type="new_complaint", workflow_hint="fraud_account_lock",
        extracted=ExtractedFields(),
    )


# ─── Resolver looks up real status (no hard-coded under_review) ──

def test_locked_account_resolved_and_does_not_loop(patch_account_repo):
    patch_account_repo(_acct("locked"))
    res = resolve_case_evidence(_SESSION, {}, _fraud_complaint())
    assert res.resolution_status == "resolved"
    assert res.public_safe_evidence["account_status"] == "locked"
    assert res.verified_status == "locked"
    # Conclusion, not interrogation — must NOT keep asking for screenshots.
    assert res.missing_info == []


def test_active_account_reported_as_active(patch_account_repo):
    patch_account_repo(_acct("active"))
    res = resolve_case_evidence(_SESSION, {}, _fraud_complaint())
    assert res.resolution_status == "resolved"
    assert res.public_safe_evidence["account_status"] == "active"


def test_status_is_normalized(patch_account_repo):
    patch_account_repo(_acct("FROZEN"))
    res = resolve_case_evidence(_SESSION, {}, _fraud_complaint())
    assert res.public_safe_evidence["account_status"] == "locked"


def test_no_account_record_is_honest_no_match(patch_account_repo):
    """Case B: claim 'bị khóa' + NO account record → say no lock record found.

    The claim is never turned into a fabricated 'under_review' status, and the
    bot must not assert any security review.
    """
    patch_account_repo(None)
    res = resolve_case_evidence(_SESSION, {}, _fraud_complaint())
    assert res.resolution_status == "no_match"
    assert res.public_safe_evidence.get("account_record_found") is False
    assert res.public_safe_evidence.get("lock_evidence_found") is False
    low = res.public_response.lower()
    assert "chưa tìm thấy bản ghi khóa" in low          # honest not-found
    assert "bảo mật xác minh" not in low                # no fake review claim
    assert "ảnh chụp" in low or "thời điểm" in low      # asks safe info only
    # Asks only safe follow-up fields.
    assert set(res.missing_info) <= {"time_locked", "screenshot"}


def test_lock_evidence_from_withdrawal_disabled_or_locked_at(patch_account_repo):
    """Lock evidence also comes from withdrawal_enabled=False / locked_at."""
    acct = _acct("active", withdrawal_enabled=False)
    patch_account_repo(acct)
    res = resolve_case_evidence(_SESSION, {}, _fraud_complaint())
    assert res.resolution_status == "resolved"
    assert res.public_safe_evidence["lock_evidence_found"] is True


def test_locked_evidence_never_exposes_fraud_signals(patch_account_repo):
    patch_account_repo(_acct("locked"))
    res = resolve_case_evidence(_SESSION, {}, _fraud_complaint())
    ev = res.public_safe_evidence
    assert "risk_score" not in ev
    assert "fraud_status" not in ev
    assert "lock_reason" not in ev          # internal reason text never exposed
    assert set(ev.keys()) <= {
        "account_status", "withdrawal_enabled", "account_record_found",
        "lock_evidence_found", "locked_at", "lock_reason_recorded",
    }


# ─── Mapper concludes locked vs active (no contradiction) ───────

def _cause(account_status):
    return to_public_safe_evidence(
        raw_evidence={"account_status": account_status},
        rule_result=None, workflow="fraud_account_lock",
        policy=load_response_policy(), resolution_status="resolved",
        missing_info=[],
    ).get("customer_safe_cause", "")


def test_mapper_locked_confirms_the_lock():
    cause = _cause("locked").lower()
    assert "tạm khóa" in cause or "bị khóa" in cause   # definite, not "đang xác minh"


def test_mapper_active_says_not_locked():
    cause = _cause("active").lower()
    assert "bình thường" in cause


def test_locked_and_active_causes_are_not_contradictory():
    assert _cause("locked") != _cause("active")


# ─── Output guardrail: lock claims need verified lock evidence ───

def _grounding(text, *, lock_evidence):
    from fintech_agent.safety.output_guardrail import check_evidence_grounding
    return check_evidence_grounding(
        text, resolver_status="no_match", verified_entity_id=None,
        verified_amount=None, fallback_text="SAFE_FALLBACK",
        lock_evidence=lock_evidence,
    )


def test_guardrail_blocks_security_review_claim_without_evidence():
    """Test 4: 'đang được bộ phận bảo mật xác minh' with no lock evidence → blocked."""
    bad = "Tài khoản của bạn đang được bộ phận bảo mật xác minh để đảm bảo an toàn."
    res = _grounding(bad, lock_evidence=False)
    assert res.is_safe is False
    assert any("unverified_lock_claim" in v for v in res.violations)
    assert res.sanitized_text == "SAFE_FALLBACK"


def test_guardrail_blocks_locked_claim_without_evidence():
    bad = "Tài khoản của bạn hiện đang bị tạm khóa."
    res = _grounding(bad, lock_evidence=False)
    assert res.is_safe is False


def test_guardrail_allows_lock_claim_with_verified_evidence():
    ok = "Tài khoản của bạn hiện đang bị tạm khóa và đang được bộ phận an ninh xem xét."
    res = _grounding(ok, lock_evidence=True)
    assert res.is_safe is True


def test_guardrail_allows_no_lock_wording_and_skips_non_account_flows():
    # Honest "not found" wording carries no lock claim → safe even without evidence.
    honest = "Hệ thống chưa tìm thấy bản ghi khóa hoặc hạn chế tài khoản."
    assert _grounding(honest, lock_evidence=False).is_safe is True
    # Non-account flows (lock_evidence=None) skip the lock check entirely.
    other = "Tài khoản của bạn hiện đang bị tạm khóa."
    assert _grounding(other, lock_evidence=None).is_safe is True
