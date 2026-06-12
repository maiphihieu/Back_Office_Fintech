"""Claim/evidence binding: customer claims can never be mixed into verified
evidence or stale diagnosis.

Covers the critical bug: account has a verified 500.000đ topup issue, customer
says "tôi nạp 5 triệu đó" — the bot must NOT confirm a 5.000.000đ transaction.

Offline/deterministic (mock repos, no LLM, no Supabase).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fintech_agent.api.generic_resolver import (
    amount_mismatch_message,
    no_match_message,
    resolve_case_evidence,
)
from fintech_agent.llm.message_analyzer import ExtractedFields, MessageAnalysis
from fintech_agent.safety.output_guardrail import (
    _extract_vnd_amounts,
    check_evidence_grounding,
)


def _txn(tid, uid, amount, status="pending"):
    return SimpleNamespace(
        transaction_id=tid, user_id=uid, amount=amount,
        service_type="wallet_topup", status=status,
        order_id=None, bill_code=None, bank_code=None,
        bank_reference=None, provider_ref_id=None, created_at="2026-06-01",
    )


@pytest.fixture
def patch_txn_repo(monkeypatch):
    from fintech_agent.repositories.base import RecordNotFound

    def _install(txns):
        repo = MagicMock()

        def _gbi(tid):
            for t in txns:
                if t.transaction_id == tid:
                    return t
            raise RecordNotFound("Transaction", "transaction_id", tid)

        repo.get_by_id.side_effect = _gbi
        repo.get_by_user_id.side_effect = lambda u: [t for t in txns if t.user_id == u]
        monkeypatch.setattr(
            "fintech_agent.database.repository_factory.get_transaction_repo",
            lambda *a, **k: repo,
        )
        return repo

    return _install


_SESSION = {"subject_type": "wallet_user", "user_id": "U1"}


def _claim(amount=None, **kw):
    return MessageAnalysis(
        message_type="provide_missing_info", workflow_hint="wallet_topup",
        extracted=ExtractedFields(amount=amount, **kw),
    )


# ─── Test 1: claimed amount ≠ verified amount → amount_mismatch ──

def test_amount_mismatch_returns_verified_not_claim(patch_txn_repo):
    patch_txn_repo([_txn("TXN_R", "U1", amount=500_000)])
    res = resolve_case_evidence(_SESSION, {}, _claim(amount=5_000_000))
    assert res.resolution_status == "amount_mismatch"
    # Verified fields hold ONLY system data — the claim is never merged in.
    assert res.verified_amount == 500_000
    assert res.resolved_entity_id == "TXN_R"
    assert res.claimed_amount == 5_000_000
    assert res.no_exact_claim_match is True


def test_mismatch_message_separates_claim_from_verified():
    msg = amount_mismatch_message("wallet_topup", 5_000_000, 500_000)
    assert "Bạn cung cấp" in msg and "5.000.000đ" in msg
    assert "theo kiểm tra hệ thống" in msg.lower() and "500.000đ" in msg
    assert "chưa tìm thấy" in msg.lower()      # claim explicitly not found
    # No fake confirmation wording for the claimed amount.
    assert "đã xác nhận" not in msg.lower()


# ─── Test 2: correction reruns resolver with the corrected amount ─

def test_corrected_amount_resolves_normally(patch_txn_repo):
    patch_txn_repo([_txn("TXN_R", "U1", amount=500_000)])
    # After "à tôi nhầm, đúng là 500k" the latest claim equals the verified amount.
    res = resolve_case_evidence(_SESSION, {}, _claim(amount=500_000))
    assert res.resolution_status == "resolved"
    assert res.resolved_entity_id == "TXN_R"
    assert res.verified_amount == 500_000
    assert res.claimed_amount is None          # no mismatch left


# ─── Test 3: no record at all → no_match, never confirmation ─────

def test_no_candidate_is_plain_no_match(patch_txn_repo):
    patch_txn_repo([])
    res = resolve_case_evidence(_SESSION, {}, _claim(amount=5_000_000))
    assert res.resolution_status == "no_match"
    assert res.verified_amount is None
    assert res.public_response == no_match_message("wallet_topup")
    assert "xác nhận" not in res.public_response.lower()


# ─── Test 4: grounding guardrail blocks fake confirmation ────────

def test_grounding_blocks_confirmation_without_verified_entity():
    bad = "Ngân hàng đã xác nhận thanh toán của bạn. Số dư ví chưa được cập nhật."
    res = check_evidence_grounding(
        bad, resolver_status="need_more_info",
        verified_entity_id=None, verified_amount=None,
        fallback_text="fallback",
    )
    assert res.is_safe is False
    assert res.sanitized_text == "fallback"
    assert any("unverified_confirmation" in v for v in res.violations)


def test_grounding_blocks_claim_amount_in_confirmed_sentence():
    # Composer wrongly states the CLAIMED 5M in a confirmed-status sentence.
    bad = "Ngân hàng đã xác nhận giao dịch nạp ví 5.000.000đ của bạn."
    res = check_evidence_grounding(
        bad, resolver_status="resolved",
        verified_entity_id="TXN_R", verified_amount=500_000,
    )
    assert res.is_safe is False
    assert any("claim_amount_in_confirmed_statement" in v for v in res.violations)


def test_grounding_allows_verified_confirmation():
    ok = (
        "Ngân hàng đã xác nhận giao dịch 500.000đ của bạn. "
        "Bạn cung cấp 5.000.000đ nhưng hệ thống chưa tìm thấy khoản này."
    )
    res = check_evidence_grounding(
        ok, resolver_status="resolved",
        verified_entity_id="TXN_R", verified_amount=500_000,
    )
    # Verified sentence uses the verified amount; the claim sentence carries
    # no confirmed wording → safe.
    assert res.is_safe is True


def test_grounding_ignores_text_without_confirmed_wording():
    res = check_evidence_grounding(
        "Chúng tôi đang kiểm tra giao dịch của bạn.",
        resolver_status="need_more_info",
        verified_entity_id=None, verified_amount=None,
    )
    assert res.is_safe is True


# ─── Test 5: repeated amount changes — each rerun is fresh ────────

def test_each_amount_change_reruns_resolver(patch_txn_repo):
    patch_txn_repo([_txn("TXN_R", "U1", amount=500_000)])
    for claimed, expected in [
        (5_000_000, "amount_mismatch"),
        (2_000_000, "amount_mismatch"),
        (500_000, "resolved"),
    ]:
        res = resolve_case_evidence(_SESSION, {}, _claim(amount=claimed))
        assert res.resolution_status == expected, claimed
        # Verified amount NEVER drifts toward the claim.
        assert res.verified_amount == 500_000


# ─── VND amount parser sanity ────────────────────────────────────

def test_vnd_amount_parser_variants():
    assert _extract_vnd_amounts("giao dịch 500.000đ") == [500_000]
    assert _extract_vnd_amounts("khoảng 5 triệu") == [5_000_000]
    assert _extract_vnd_amounts("500k qua VCB") == [500_000]
    assert _extract_vnd_amounts("1,5 triệu đồng") == [1_500_000]
    assert _extract_vnd_amounts("không có số tiền nào") == []
