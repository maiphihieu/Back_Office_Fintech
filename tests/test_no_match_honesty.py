"""No-match honesty: the agent must say clearly when the logged-in account has
no matching data, and never fake-confirm a transaction that cannot be verified.

Most tests are offline/deterministic: the resolver + ownership + ticket-gating
logic is exercised with a mock transaction repository (no Supabase, no LLM).
Two end-to-end checks drive the pipeline through TestClient.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from fintech_agent.api.generic_resolver import (
    NO_MATCH_ID_RESPONSE,
    NO_MATCH_INSIST_RESPONSE,
    no_match_message,
    resolve_case_evidence,
)
from fintech_agent.llm.message_analyzer import (
    ActiveCaseContext,
    ExtractedFields,
    MessageAnalysis,
)


# ─── Mock transaction repository ─────────────────────────────────

def _txn(transaction_id, user_id, **kw):
    return SimpleNamespace(
        transaction_id=transaction_id, user_id=user_id,
        amount=kw.get("amount", 500000),
        service_type=kw.get("service_type", "wallet_topup"),
        status=kw.get("status", "pending"),
        bank_code=kw.get("bank_code"), order_id=kw.get("order_id"),
        bill_code=kw.get("bill_code"), provider_ref_id=kw.get("provider_ref_id"),
        bank_reference=kw.get("bank_reference"), created_at=kw.get("created_at", ""),
    )


@pytest.fixture
def patch_txn_repo(monkeypatch):
    from fintech_agent.repositories.base import RecordNotFound

    def _install(txns):
        repo = MagicMock()

        def _get_by_id(tid):
            for t in txns:
                if t.transaction_id == tid:
                    return t
            raise RecordNotFound("Transaction", "transaction_id", tid)

        repo.get_by_id.side_effect = _get_by_id
        repo.get_by_user_id.side_effect = lambda uid: [t for t in txns if t.user_id == uid]
        monkeypatch.setattr(
            "fintech_agent.database.repository_factory.get_transaction_repo",
            lambda *a, **k: repo,
        )
        return repo

    return _install


_WALLET_SESSION = {"subject_type": "wallet_user", "user_id": "U1", "wallet_id": "W1"}


def _analysis(**extracted):
    return MessageAnalysis(
        message_type="provide_missing_info",
        workflow_hint="wallet_topup",
        extracted=ExtractedFields(**extracted),
    )


# ─── Resolver: honest no-match messages ──────────────────────────

def test_id_not_found_in_account_says_so(patch_txn_repo):
    """Test A: a transaction_id that does not exist on the account → clear message."""
    patch_txn_repo([])  # account has no transactions at all
    res = resolve_case_evidence(_WALLET_SESSION, {}, _analysis(transaction_id="TXN_NOPE"))
    assert res.resolution_status == "no_match"
    assert res.public_response == NO_MATCH_ID_RESPONSE
    # Never fake-confirm: no verified evidence is produced.
    assert res.verified_amount is None
    assert res.verified_status == ""
    assert res.resolved_entity_id is None


def test_amount_time_bank_no_match_surfaces_real_record(patch_txn_repo):
    """Test B (updated contract): claimed amount matches nothing, but the
    account HAS a problematic top-up → amount_mismatch surfaces the verified
    record instead of hiding it. The claim is never merged into evidence."""
    # The account has a real 500k top-up, but the customer claims 300k via ACB.
    patch_txn_repo([_txn("TXN_REAL", "U1", amount=500000, bank_code="VCB")])
    res = resolve_case_evidence(
        _WALLET_SESSION, {},
        _analysis(amount=300000, bank_name="ACB", approximate_time_text="9h sáng"),
    )
    assert res.resolution_status == "amount_mismatch"
    assert res.verified_amount == 500000       # system data, not the claim
    assert res.claimed_amount == 300000        # claim kept separate
    assert res.no_exact_claim_match is True
    # Must NOT claim the bank confirmed anything in any canned text.
    assert "đã xác nhận" not in (res.public_response or "")


def test_amount_no_match_when_account_has_nothing(patch_txn_repo):
    """Claimed amount + truly empty account → plain honest no_match."""
    patch_txn_repo([])
    res = resolve_case_evidence(
        _WALLET_SESSION, {},
        _analysis(amount=300000, bank_name="ACB", approximate_time_text="9h sáng"),
    )
    assert res.resolution_status == "no_match"
    assert res.public_response == no_match_message("wallet_topup")
    assert "tài khoản đang đăng nhập" in res.public_response
    assert res.verified_bank_status == ""


def test_id_belonging_to_another_user_is_not_resolved(patch_txn_repo):
    """The resolver only matches the logged-in account's own transactions."""
    patch_txn_repo([_txn("TXN_OTHER", "U_OTHER", amount=500000)])
    res = resolve_case_evidence(_WALLET_SESSION, {}, _analysis(transaction_id="TXN_OTHER"))
    assert res.resolution_status == "no_match"      # not someone else's record
    assert res.resolved_entity_id is None


def test_no_match_produces_no_verified_evidence(patch_txn_repo):
    patch_txn_repo([])
    res = resolve_case_evidence(_WALLET_SESSION, {}, _analysis(amount=999000))
    assert res.resolution_status == "no_match"
    assert res.public_safe_evidence == {}
    assert res.verified_amount is None


# ─── Test D: ownership — no cross-account data leak ──────────────

def test_ownership_mismatch_blocks_and_does_not_leak():
    from fintech_agent.api.customer_chat import (
        _WALLET_OWNERSHIP_MISMATCH_RESPONSE,
        _validate_wallet_user_ownership,
    )
    repo = MagicMock()
    repo.get_by_id.return_value = _txn("TXN_OTHER_999", "U_OTHER", amount=500000)
    with patch("fintech_agent.api.customer_chat.get_transaction_repo", return_value=repo):
        msg = "Giao dịch TXN_OTHER_999 của tôi nạp chưa vào ví"
        result = _validate_wallet_user_ownership(msg, {"user_id": "U1"})
    assert result == _WALLET_OWNERSHIP_MISMATCH_RESPONSE
    # The refusal must not leak the other account's data (amount/owner/status).
    assert "500000" not in result and "U_OTHER" not in result


# ─── Rule 5: no_match does not create a staff ticket ─────────────

def test_no_match_does_not_create_staff_ticket():
    from fintech_agent.api.customer_chat import _should_create_ticket
    from fintech_agent.api.generic_resolver import ResolutionResult

    ctx = ActiveCaseContext(case_id="CASE_X", selected_workflow="wallet_topup")
    res = ResolutionResult(resolution_status="no_match")
    assert _should_create_ticket(
        MessageAnalysis(message_type="follow_up"), res, None, ctx,
    ) is False


def test_no_match_with_rule_review_still_creates_ticket():
    """If a rule decision requires review, a ticket is still warranted."""
    from fintech_agent.api.customer_chat import _should_create_ticket
    from fintech_agent.api.generic_resolver import ResolutionResult

    ctx = ActiveCaseContext(case_id="CASE_X", selected_workflow="wallet_topup")
    res = ResolutionResult(resolution_status="need_more_info")
    assert _should_create_ticket(
        MessageAnalysis(message_type="follow_up"), res,
        {"rule_decision": {"action": "manual_review"}}, ctx,
    ) is True


# ─── Insistence message wording (Rule 4) ─────────────────────────

def test_insist_message_is_honest_and_safe():
    text = NO_MATCH_INSIST_RESPONSE.lower()
    assert "chưa tìm thấy giao dịch khớp" in text   # does not confirm it exists
    assert "biên lai" in text or "tham chiếu" in text   # asks for stronger evidence
    assert "pin" in text and "otp" in text          # safety reminder
    # Must NOT claim confirmation/processing of a transaction.
    assert "đã xác nhận" not in text
    assert "đang xử lý giao dịch này" not in text
