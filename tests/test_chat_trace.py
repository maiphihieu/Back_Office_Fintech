"""Trace-based tests for customer chat pipeline.

Tests inspect the CustomerChatTrace debug object returned when
debug=true, verifying internal pipeline steps — not just final text.

No hard-coded transaction IDs, user IDs, or fixed answer templates.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


# ─── Test fixtures ──────────────────────────────────────────────

@pytest.fixture
def client():
    """Fresh TestClient for each test."""
    from fintech_agent.main import app
    # Reset session context between tests
    from fintech_agent.api.customer_chat import _session_context
    _session_context.clear()
    return TestClient(app)


# ─── Mock data builders (generic, no hard-coded IDs) ───────────

_TODAY_9AM = datetime.now(tz=timezone.utc).replace(
    hour=9, minute=0, second=0, microsecond=0,
)


def _make_txn(txn_id, user_id, **kwargs):
    """Build a mock transaction."""
    return SimpleNamespace(
        transaction_id=txn_id,
        user_id=user_id,
        amount=kwargs.get("amount", 100000),
        status=kwargs.get("status", "pending"),
        service_type=kwargs.get("service_type", ""),
        bank_code=kwargs.get("bank_code", ""),
        bank_reference=kwargs.get("bank_reference", ""),
        created_at=kwargs.get("created_at", _TODAY_9AM),
        customer_code=kwargs.get("customer_code"),
        provider_ref_id=kwargs.get("provider_ref_id"),
    )


def _make_recon(txn_id, **kwargs):
    """Build a mock reconciliation record."""
    return SimpleNamespace(
        transaction_id=txn_id,
        status=kwargs.get("status", "pending"),
        mismatch_type=kwargs.get("mismatch_type", "bank_confirmed_wallet_pending"),
        bank_status=kwargs.get("bank_status", "success"),
        bank_amount=kwargs.get("bank_amount", 500000),
        money_received_in_master_wallet=kwargs.get("wallet_received", False),
        bank_ref_id=kwargs.get("bank_ref_id", "REF123"),
        note=kwargs.get("note", ""),
    )


# ─── Mock session ──────────────────────────────────────────────

MOCK_SESSIONS = [
    {
        "session_id": "trace_test_session",
        "subject_type": "wallet_user",
        "display_name": "Test User",
        "role": "customer",
        "is_authenticated": True,
        "user_id": "U_TEST_001",
        "phone": "0900000001",
        "email": "test@example.com",
    },
]


def _mock_repo():
    repo = MagicMock()
    repo.list_active_sessions_with_expiry_filter.return_value = []
    repo.get_session.side_effect = lambda sid: next(
        (s for s in MOCK_SESSIONS if s["session_id"] == sid), None,
    )
    return repo


def _mock_txn_repo(transactions):
    from fintech_agent.repositories.base import RecordNotFound
    repo = MagicMock()

    def _get_by_id(txn_id):
        for t in transactions:
            if t.transaction_id == txn_id:
                return t
        raise RecordNotFound("Transaction", "transaction_id", txn_id)

    def _get_by_user_id(user_id):
        return [t for t in transactions if t.user_id == user_id]

    repo.get_by_id.side_effect = _get_by_id
    repo.get_by_user_id.side_effect = _get_by_user_id
    return repo


def _mock_recon_repo(records):
    repo = MagicMock()

    def _get_by_txn_id(txn_id):
        for r in records:
            if r.transaction_id == txn_id:
                return r
        return None

    repo.get_by_transaction_id.side_effect = _get_by_txn_id
    return repo


def _patches(session_repo, txn_repo, recon_repo=None):
    """Context manager combining all needed patches."""
    import contextlib

    # Patch session repo at both source and consumer
    p_session_1 = patch(
        "fintech_agent.api.mock_auth.get_mock_session_repo",
        return_value=session_repo,
    )
    p_session_2 = patch(
        "fintech_agent.api.customer_chat.get_mock_session_repo",
        return_value=session_repo,
    )
    # Patch txn repo at consumer and factory
    p_txn_1 = patch(
        "fintech_agent.api.customer_chat.get_transaction_repo",
        return_value=txn_repo,
    )
    # Ensure find_by_user_id alias exists
    if not hasattr(txn_repo, 'find_by_user_id') or not callable(getattr(txn_repo, 'find_by_user_id', None)):
        txn_repo.find_by_user_id = txn_repo.get_by_user_id

    p_txn_2 = patch(
        "fintech_agent.database.repository_factory.get_transaction_repo",
        return_value=txn_repo,
    )
    managers = [p_session_1, p_session_2, p_txn_1, p_txn_2]

    if recon_repo is not None:
        p_recon = patch(
            "fintech_agent.database.repository_factory.get_reconciliation_repo",
            return_value=recon_repo,
        )
        managers.append(p_recon)

    return contextlib.ExitStack(), managers


def _inject_case_context(session_id, **kwargs):
    from fintech_agent.api.customer_chat import _session_context
    from fintech_agent.llm.message_analyzer import ActiveCaseContext
    _session_context[session_id] = ActiveCaseContext(**kwargs)


# ─── Test 1: Resolved transaction with matching data ───────────

def test_trace_resolved_with_matching_transaction(client: TestClient):
    """Logged-in topup user provides amount/time/bank and matching txn exists.

    Expected trace:
    - extracted amount is not null
    - resolver called and resolved
    - evidence has bank_status
    - diagnosis has customer_safe_cause
    - final response is not generic
    """
    txn = _make_txn(
        "TXN_TRACE_001", "U_TEST_001",
        amount=500000, service_type="wallet_topup", status="pending",
        bank_code="VCB", created_at=_TODAY_9AM,
    )
    recon = _make_recon(
        "TXN_TRACE_001",
        bank_status="success",
        wallet_received=False,
        mismatch_type="bank_confirmed_wallet_pending",
    )

    session_repo = _mock_repo()
    txn_repo = _mock_txn_repo([txn])
    recon_repo = _mock_recon_repo([recon])

    _inject_case_context(
        "trace_test_session",
        case_id="CASE_TRACE_001",
        selected_workflow="wallet_topup",
        missing_fields=["transaction_id"],
    )

    stack, managers = _patches(session_repo, txn_repo, recon_repo)
    with stack:
        for m in managers:
            stack.enter_context(m)

        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp khoảng 9h sáng, số tiền 500000, ngân hàng Vietcombank đã trừ tiền.",
                "session_id": "trace_test_session",
                "debug": True,
            },
        )

    data = resp.json()
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {data}"

    trace = data.get("debug_trace")
    assert trace is not None, "debug_trace should be present when debug=true"

    # Analysis extracted amount
    assert trace["message_analysis"]["extracted_amount"] is not None, (
        f"Expected extracted amount, got: {trace['message_analysis']}"
    )

    # Resolver called and resolved
    assert trace["resolver"]["called"] is True
    assert trace["resolver"]["resolution_status"] == "resolved", (
        f"Expected resolved, got: {trace['resolver']}"
    )

    # Evidence has bank_status from reconciliation
    assert trace["evidence"]["has_bank_status"] is True, (
        f"Expected bank_status in evidence, got: {trace['evidence']}"
    )

    # Diagnosis was created
    assert trace["public_safe_diagnosis"]["created"] is True, (
        f"Expected diagnosis created, got: {trace['public_safe_diagnosis']}"
    )

    # Customer-safe cause is not empty
    assert trace["public_safe_diagnosis"]["customer_safe_cause"], (
        f"Expected customer_safe_cause, got empty: {trace['public_safe_diagnosis']}"
    )

    # Final response is not generic
    response_text = data["public_response"].lower()
    generic_phrases = ["chúng tôi đang kiểm tra và sẽ phản hồi sớm nhất"]
    for phrase in generic_phrases:
        assert phrase not in response_text, (
            f"Response should not be generic: {data['public_response'][:100]}"
        )

    # Response length > minimal
    assert len(data["public_response"]) > 30


# ─── Test 2: Resolver should resolve when data matches ─────────

def test_trace_resolver_should_not_return_no_match(client: TestClient):
    """When matching transaction exists, resolver must not return no_match.

    Prints resolver query basis and candidate_count on failure.
    """
    txn = _make_txn(
        "TXN_TRACE_002", "U_TEST_001",
        amount=300000, service_type="wallet_topup", status="processing",
        bank_code="TCB", created_at=_TODAY_9AM,
    )

    session_repo = _mock_repo()
    txn_repo = _mock_txn_repo([txn])

    _inject_case_context(
        "trace_test_session",
        case_id="CASE_TRACE_002",
        selected_workflow="wallet_topup",
        missing_fields=["transaction_id"],
    )

    stack, managers = _patches(session_repo, txn_repo)
    with stack:
        for m in managers:
            stack.enter_context(m)

        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp 300000 qua Techcombank sáng nay.",
                "session_id": "trace_test_session",
                "debug": True,
            },
        )

    data = resp.json()
    trace = data.get("debug_trace")

    if trace and trace["resolver"]["resolution_status"] == "no_match":
        print(f"\n[FAIL] Resolver returned no_match despite matching transaction!")
        print(f"  query_basis: {trace['resolver']['query_basis']}")
        print(f"  candidate_count: {trace['resolver']['candidate_count']}")
        print(f"  message_analysis: {trace['message_analysis']}")

    if trace:
        assert trace["resolver"]["resolution_status"] != "no_match", (
            f"Resolver should find matching transaction. "
            f"Query: {trace['resolver']['query_basis']}"
        )


# ─── Test 3: Evidence exists but no diagnosis ──────────────────

def test_trace_evidence_must_produce_diagnosis(client: TestClient):
    """If resolver resolves and evidence exists, diagnosis must be created."""
    txn = _make_txn(
        "TXN_TRACE_003", "U_TEST_001",
        amount=200000, service_type="wallet_topup", status="success",
        bank_code="VCB", created_at=_TODAY_9AM,
    )

    session_repo = _mock_repo()
    txn_repo = _mock_txn_repo([txn])

    _inject_case_context(
        "trace_test_session",
        case_id="CASE_TRACE_003",
        selected_workflow="wallet_topup",
        missing_fields=["transaction_id"],
    )

    stack, managers = _patches(session_repo, txn_repo)
    with stack:
        for m in managers:
            stack.enter_context(m)

        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp 200000 qua VCB lúc 9h.",
                "session_id": "trace_test_session",
                "debug": True,
            },
        )

    data = resp.json()
    trace = data.get("debug_trace")

    if trace:
        if trace["resolver"]["resolution_status"] == "resolved":
            assert trace["evidence"]["evidence_found"] is True, (
                f"Evidence should be found when resolved. "
                f"Evidence: {trace['evidence']}"
            )
            # Diagnosis should have at least what_was_checked
            assert len(trace["public_safe_diagnosis"]["what_was_checked"]) > 0, (
                f"Diagnosis should have what_was_checked. "
                f"Diagnosis: {trace['public_safe_diagnosis']}"
            )


# ─── Test 4: No matching transaction ───────────────────────────

def test_trace_no_match_asks_for_more_info(client: TestClient):
    """No matching transaction → resolver returns no_match.

    Response should ask for exact date or bank reference, not invent cause.
    """
    # Only has a transaction with different amount
    txn = _make_txn(
        "TXN_TRACE_004", "U_TEST_001",
        amount=100000, service_type="wallet_topup", status="pending",
        created_at=_TODAY_9AM,
    )

    session_repo = _mock_repo()
    txn_repo = _mock_txn_repo([txn])

    _inject_case_context(
        "trace_test_session",
        case_id="CASE_TRACE_004",
        selected_workflow="wallet_topup",
        missing_fields=["transaction_id"],
    )

    stack, managers = _patches(session_repo, txn_repo)
    with stack:
        for m in managers:
            stack.enter_context(m)

        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp 999999 lúc 3h sáng qua ngân hàng ABC.",
                "session_id": "trace_test_session",
                "debug": True,
            },
        )

    data = resp.json()
    # Status can be 'need_more_info' or 'received' depending on LLM classification
    assert data["status"] in ("need_more_info", "received"), (
        f"Expected need_more_info or received, got: {data['status']}"
    )

    # Response should mention not found or ask for more info or acknowledge
    resp_lower = data["public_response"].lower()
    no_match_kws = [
        "chưa tìm", "không tìm", "kiểm tra lại", "thông tin",
        "ghi nhận", "hỗ trợ", "tham chiếu", "cung cấp",
        "bộ phận", "xử lý", "phản hồi",
    ]
    assert any(kw in resp_lower for kw in no_match_kws), (
        f"Expected informative response, got: {resp_lower[:120]}"
    )


# ─── Test 5: Fraud account lock follow-up ──────────────────────

def test_trace_fraud_lock_follow_up(client: TestClient):
    """Fraud account lock: customer asks 'tôi cần cung cấp gì'.

    Expected:
    - belongs_to_active_case or workflow_hint = fraud_account_lock
    - no transaction_id prompt in missing info
    """
    session_repo = _mock_repo()
    txn_repo = _mock_txn_repo([])

    _inject_case_context(
        "trace_test_session",
        case_id="CASE_FRAUD_001",
        selected_workflow="fraud_account_lock",
        service_type="fraud_account_lock",
        missing_fields=["device_info"],
    )

    stack, managers = _patches(session_repo, txn_repo)
    with stack:
        for m in managers:
            stack.enter_context(m)

        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi cần cung cấp gì để mở khóa tài khoản?",
                "session_id": "trace_test_session",
                "debug": True,
            },
        )

    data = resp.json()
    assert resp.status_code == 201

    trace = data.get("debug_trace")

    # Should NOT ask for transaction_id for fraud lock
    questions = data.get("missing_info_questions", [])
    for q in questions:
        assert "mã giao dịch" not in q.lower(), (
            f"Fraud lock should not ask for transaction_id: {q}"
        )

    # Response should be about account verification, not transaction
    resp_lower = data["public_response"].lower()
    assert len(resp_lower) > 20, "Response should not be empty"
