"""Train-ticket: the agent searches the logged-in account before asking.

A paid train ticket whose ticket was never issued is the canonical
"đã thanh toán nhưng chưa nhận vé" complaint. Payment status alone is
'completed', so the resolver must consult the provider record to know the
transaction still needs attention — and answer from it instead of asking the
customer for a transaction code.

Resolver-level tests are offline/deterministic (mock repo + patched provider
lookup). One end-to-end test drives the real pipeline.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from fintech_agent.api.generic_resolver import (
    _transaction_needs_attention,
    no_match_message,
    resolve_case_evidence,
)
from fintech_agent.llm.message_analyzer import ExtractedFields, MessageAnalysis


def _train_txn(tid, uid="U1", status="completed", ref="REF1"):
    return SimpleNamespace(
        transaction_id=tid, user_id=uid, amount=450000,
        service_type="train_ticket", status=status, provider_ref_id=ref,
        order_id=None, bill_code=None, bank_code=None, bank_reference=None,
        created_at="2026-05-28T08:00:00Z",
    )


_SESSION = {"subject_type": "wallet_user", "user_id": "U1"}


def _complaint():
    # workflow already classified as train_ticket (the LLM's job; tested live)
    return MessageAnalysis(
        message_type="new_complaint", workflow_hint="train_ticket",
        extracted=ExtractedFields(),  # no transaction_id / no criteria
    )


@pytest.fixture
def patch_repo(monkeypatch):
    from fintech_agent.repositories.base import RecordNotFound

    def _install(txns, *, ticket_status="not_issued"):
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
        # Provider says the ticket was (not) issued.
        monkeypatch.setattr(
            "fintech_agent.api.generic_resolver._lookup_train_provider",
            lambda txn: {
                "ticket_status": ticket_status,
                "provider_status": "confirmed" if ticket_status == "issued" else "not_confirmed",
            },
        )
        return repo

    return _install


# ─── 'needs attention' is provider-aware, not payment-only ───────

def test_paid_but_ticket_not_issued_needs_attention(patch_repo):
    patch_repo([], ticket_status="not_issued")
    assert _transaction_needs_attention(_train_txn("T1")) is True


def test_paid_and_ticket_issued_is_settled(patch_repo):
    patch_repo([], ticket_status="issued")
    assert _transaction_needs_attention(_train_txn("T1")) is False


# ─── Test A & C: resolver runs before asking, finds the record ───

def test_resolver_finds_paid_but_no_ticket_without_asking(patch_repo):
    patch_repo([_train_txn("TXN_TRAIN_1")], ticket_status="not_issued")
    res = resolve_case_evidence(_SESSION, {}, _complaint())
    assert res.resolution_status == "resolved"          # searched, not asked
    assert res.resolved_entity_id == "TXN_TRAIN_1"
    assert res.verified_status == "completed"            # payment IS verified
    # Train evidence — provider/ticket status, never wallet wording.
    assert "ticket_status" in res.public_safe_evidence
    assert "wallet_status" not in res.public_safe_evidence


# ─── Test B: no train record on the account ──────────────────────

def test_no_train_record_says_not_found(patch_repo):
    patch_repo([])  # account has no train transactions
    res = resolve_case_evidence(_SESSION, {}, _complaint())
    assert res.resolution_status == "no_match"
    assert res.public_response == no_match_message("train_ticket")
    assert "vé tàu" in res.public_response
    assert "tài khoản đang đăng nhập" in res.public_response
    # Must NOT pretend payment was confirmed.
    assert "đã xác nhận thanh toán" not in res.public_response


# ─── Test D: multiple train records → ask one narrowing field ────

def test_multiple_train_records_ask_one_narrowing_field(patch_repo):
    patch_repo(
        [_train_txn("T1", ref="R1"), _train_txn("T2", ref="R2")],
        ticket_status="not_issued",
    )
    res = resolve_case_evidence(_SESSION, {}, _complaint())
    assert res.resolution_status == "multiple_candidates"
    assert "2" in res.public_response and "vé tàu" in res.public_response
    # Asks for a single narrowing field, not the whole form.
    assert res.missing_info == ["transaction_time"]


# ─── Test C': resolved train reply carries no wallet wording ─────

def test_resolved_train_evidence_has_no_wallet_fields(patch_repo):
    patch_repo([_train_txn("T1")], ticket_status="not_issued")
    res = resolve_case_evidence(_SESSION, {}, _complaint())
    ev = res.public_safe_evidence
    assert "wallet_status" not in ev          # no "ví đã nhận tiền" path
    assert ev.get("ticket_status") == "not_issued"


# ─── Test A end-to-end: pipeline searches before asking ──────────

def test_pipeline_train_complaint_resolves_before_asking(patch_repo):
    """End-to-end: a logged-in train user's complaint is diagnosed from their
    own record — the bot does not ask for a transaction code."""
    from fastapi.testclient import TestClient
    from fintech_agent.main import create_app
    import fintech_agent.api.customer_chat as cc

    patch_repo([_train_txn("TXN_TRAIN_1", uid="U_TRAIN")], ticket_status="not_issued")

    sess = {
        "session_id": "sess_train", "subject_type": "wallet_user",
        "display_name": "Khách vé tàu", "user_id": "U_TRAIN",
        "wallet_id": "W_TRAIN", "phone": "0900", "email": "t@example.com",
    }
    session_repo = MagicMock()
    session_repo.get_session.return_value = sess

    cc._session_context.clear()
    with patch.object(cc, "get_mock_session_repo", return_value=session_repo):
        client = TestClient(create_app())
        body = client.post("/api/customer-chat", json={
            "message": "sao tôi thanh toán vé tàu rồi mà tôi vẫn chưa nhận được vé",
            "session_id": "sess_train",
        }).json()

    text = body["public_response"].lower()
    questions = " ".join(body.get("missing_info_questions", [])).lower()
    # The bot must NOT demand a transaction code — it already found the record.
    assert "cung cấp mã giao dịch" not in text
    assert "transaction_id" not in text and "transaction_id" not in questions
    # Train-ticket framing, never wallet wording.
    for forbidden in ("số dư ví", "nạp ví", "ví đã nhận tiền", "kiểm tra lại số dư"):
        assert forbidden not in text
