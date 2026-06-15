"""Pipeline-level: when the LLM analyzer is uncertain (workflow_hint='unknown')
the bot must still scope evidence to the workflow the LATEST message is about —
never answer from another workflow's data that happens to exist on the account.

This reproduces the live bug: a train-ticket demo account, customer complains
about a wallet top-up, the analyzer returns 'unknown', and the proactive scan
must NOT surface the account's train transaction as the answer.

Integration-style: patches only the message analyzer (to force 'unknown'); the
resolver, graph and guardrails run for real.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import fintech_agent.api.customer_chat as cc
from fintech_agent.llm.message_analyzer import ExtractedFields, MessageAnalysis
from fintech_agent.main import create_app


@pytest.fixture
def client():
    cc._session_context.clear()
    cc._session_last_active.clear()
    return TestClient(create_app())


def _unknown_analysis(*_a, **_k):
    """Simulate an uncertain analyzer: clear intent text but workflow_hint=unknown."""
    return MessageAnalysis(
        message_type="new_complaint",
        workflow_hint="unknown",          # the bug trigger
        belongs_to_active_case=False,
        confidence=0.5,
        extracted=ExtractedFields(),
    )


_TRAIN_WORDING = ("vé", "nhà cung cấp", "đối soát vé", "ticket")
_WALLET_WORDING = ("nạp tiền", "nạp ví", "ví")


def test_wallet_complaint_on_train_account_never_uses_train_data(client):
    """Analyzer unknown + wallet complaint on a train-only account →
    honest wallet no-match, no train wording, no generic template."""
    with patch.object(cc, "analyze_customer_message", _unknown_analysis):
        body = client.post("/api/customer-chat", json={
            "message": "tôi nạp tiền vào tài khoản nhưng ví không nhận",
            "session_id": "demo_customer_train_002",
        }).json()

    resp = body["public_response"].lower()
    # Must NOT answer with train-ticket wording from the account's train data.
    for w in _TRAIN_WORDING:
        assert w not in resp, f"train wording '{w}' leaked: {resp[:160]}"
    # Must NOT be the generic catch-all template.
    assert "bộ phận hỗ trợ sẽ kiểm tra và phản hồi" not in resp
    # Honest, wallet-scoped, not-found answer.
    assert "chưa tìm thấy" in resp
    assert "tài khoản đang đăng nhập" in resp


def test_train_complaint_on_train_account_still_resolves(client):
    """Control: a genuine train complaint on the train account still uses the
    train resolver (the fix must not over-suppress)."""
    with patch.object(cc, "analyze_customer_message", _unknown_analysis):
        body = client.post("/api/customer-chat", json={
            "message": "tôi thanh toán vé tàu rồi nhưng chưa nhận được vé",
            "session_id": "demo_customer_train_002",
        }).json()

    resp = body["public_response"].lower()
    # The account HAS a train issue → train-scoped answer is allowed here.
    assert "vé" in resp
    # And must not leak wallet-balance wording for a train workflow.
    assert "số dư ví" not in resp
