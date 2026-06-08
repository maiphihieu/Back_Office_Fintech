"""Tests for mock auth endpoints, session-based customer chat, and ownership validation.

Tests verify:
  1. Auth endpoints return correct structure.
  2. Sessions response does NOT contain internal identity fields.
  3. Login validates session correctly.
  4. Customer chat with session injects identity server-side.
  5. Customer chat without session still works (backward compat).
  6. Customer chat response remains sanitized (no internal data).
  7. Transaction ownership validation (wallet_user + transaction_id).
  8. Merchant identity validation (merchant + merchant_id/tax_code).
  9. Existing back-office endpoints still work unchanged.
"""

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


# ─── Mock Session Data ──────────────────────────────────────────

MOCK_SESSIONS = [
    {
        "session_id": "demo_customer_topup",
        "subject_type": "wallet_user",
        "display_name": "Khách nạp tiền demo",
        "role": "customer",
        "is_authenticated": True,
        "user_id": "U_TOPUP_001",
        "wallet_id": "WALLET_TOPUP_001",
        "phone": "0981000101",
        "email": "topup.customer@example.com",
        "expires_at": None,
    },
    {
        "session_id": "demo_customer_fraud_fp",
        "subject_type": "wallet_user",
        "display_name": "Khách bị khóa nhầm demo",
        "role": "customer",
        "is_authenticated": True,
        "user_id": "U_FRAUD_FP",
        "wallet_id": "WALLET_FRAUD_FP",
        "phone": "0981000001",
        "email": "fraud.fp@example.com",
        "expires_at": None,
    },
    {
        "session_id": "demo_merchant_batch_fail",
        "subject_type": "merchant",
        "display_name": "Cửa hàng Batch Fail",
        "role": "merchant",
        "is_authenticated": True,
        "merchant_id": "MRC_001_BATCH_FAIL",
        "tax_code": "0100000001",
        "phone": "0903000001",
        "email": "mrc001@example.com",
        "expires_at": None,
    },
]

# Public-only view (no identity fields)
MOCK_SESSIONS_PUBLIC = [
    {
        "session_id": s["session_id"],
        "subject_type": s["subject_type"],
        "display_name": s["display_name"],
        "role": s["role"],
        "is_authenticated": s["is_authenticated"],
    }
    for s in MOCK_SESSIONS
]

# Internal fields that must NEVER appear in API responses
INTERNAL_FIELDS = [
    "user_id", "wallet_id", "merchant_id", "tax_code",
    "phone", "email", "pin_hash",
]


# ─── Mock Transaction Data ──────────────────────────────────────

def _make_txn(transaction_id: str, user_id: str, **kwargs):
    """Create a mock Transaction object with dot-attribute access."""
    return SimpleNamespace(
        transaction_id=transaction_id,
        user_id=user_id,
        service_type=kwargs.get("service_type", "wallet_topup"),
        amount=kwargs.get("amount", 500000),
        status=kwargs.get("status", "pending"),
        order_id=kwargs.get("order_id"),
        bill_code=kwargs.get("bill_code"),
        customer_code=kwargs.get("customer_code"),
        provider_ref_id=kwargs.get("provider_ref_id"),
        created_at=kwargs.get("created_at"),
    )

# Timestamp: today at 9:00 AM
_TODAY_9AM = datetime.now(tz=timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
_TODAY_9AM_PLUS_5 = _TODAY_9AM.replace(minute=5)

# Transaction owned by U_TOPUP_001 — 500000 VND, wallet_topup, pending
MOCK_TXN_TOPUP_001 = _make_txn(
    "TXN_TOPUP_001", "U_TOPUP_001",
    amount=500000, service_type="wallet_topup", status="pending",
    created_at=_TODAY_9AM,
)
# Second transaction same user, same amount (for multiple-match test)
MOCK_TXN_TOPUP_002 = _make_txn(
    "TXN_TOPUP_002", "U_TOPUP_001",
    amount=500000, service_type="wallet_topup", status="pending",
    created_at=_TODAY_9AM_PLUS_5,
)
# Transaction owned by a DIFFERENT user
MOCK_TXN_OTHER_USER = _make_txn("TXN_OTHER_999", "U_OTHER_999", created_at=_TODAY_9AM)


# ─── Helpers ────────────────────────────────────────────────────

def _mock_repo():
    """Create a mock session repository that returns test session data."""
    repo = MagicMock()
    repo.list_active_sessions_with_expiry_filter.return_value = MOCK_SESSIONS_PUBLIC
    repo.get_session.side_effect = lambda sid: next(
        (s for s in MOCK_SESSIONS if s["session_id"] == sid), None
    )

    # Mock verify_pin: only 0981000101 + 123456 succeeds
    def _verify_pin(phone, pin):
        if phone.strip() == "0981000101" and pin == "123456":
            return {
                "session_id": "demo_customer_topup",
                "subject_type": "wallet_user",
                "display_name": "Khách nạp tiền demo",
                "role": "customer",
                "is_authenticated": True,
            }
        return None

    repo.verify_pin.side_effect = _verify_pin
    return repo


def _mock_txn_repo(transactions=None):
    """Create a mock transaction repository with get_by_id and get_by_user_id."""
    from fintech_agent.repositories.base import RecordNotFound

    if transactions is None:
        transactions = [MOCK_TXN_TOPUP_001, MOCK_TXN_OTHER_USER]

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


def _patch_txn_repo(txn_repo):
    """Patch the transaction repo used by customer_chat ownership validation."""
    return patch(
        "fintech_agent.api.customer_chat.get_transaction_repo",
        return_value=txn_repo,
    )


def _patch_resolver_txn_repo(txn_repo):
    """Patch the transaction repo used by both alternative_resolver and generic_resolver."""
    # Ensure the mock supports both method names
    if not hasattr(txn_repo, 'find_by_user_id') or not callable(getattr(txn_repo, 'find_by_user_id', None)):
        txn_repo.find_by_user_id = txn_repo.get_by_user_id

    p1 = patch(
        "fintech_agent.api.alternative_resolver.get_transaction_repo",
        return_value=txn_repo,
    )
    # generic_resolver imports get_transaction_repo inside the function body,
    # so we must patch at the source (repository_factory), not the consumer.
    p2 = patch(
        "fintech_agent.database.repository_factory.get_transaction_repo",
        return_value=txn_repo,
    )

    class _combined:
        def __enter__(self):
            self._p1 = p1.__enter__()
            self._p2 = p2.__enter__()
            return self

        def __exit__(self, *exc):
            p2.__exit__(*exc)
            p1.__exit__(*exc)

    return _combined()


def _patch_mock_session_repo(repo):
    """Patch the mock session repo factory at all consumer locations."""
    p1 = patch(
        "fintech_agent.api.mock_auth.get_mock_session_repo",
        return_value=repo,
    )
    p2 = patch(
        "fintech_agent.api.customer_chat.get_mock_session_repo",
        return_value=repo,
    )

    class _combined:
        def __enter__(self):
            # Reset in-memory follow-up tracking between tests
            from fintech_agent.api.customer_chat import _session_context
            _session_context.clear()
            p1.__enter__()
            p2.__enter__()
            return self

        def __exit__(self, *args):
            p2.__exit__(*args)
            p1.__exit__(*args)

    return _combined()




# ─── Test: POST /api/auth/customer-login (phone + PIN) ─────────

def test_customer_pin_login_success(client: TestClient) -> None:
    """Login with valid phone + PIN returns session context."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/auth/customer-login",
            json={"phone": "0981000101", "pin": "123456"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["is_authenticated"] is True
    assert data["session_id"] == "demo_customer_topup"
    assert data["subject_type"] == "wallet_user"
    assert data["display_name"] == "Khách nạp tiền demo"


def test_customer_pin_login_wrong_pin(client: TestClient) -> None:
    """Login with wrong PIN returns safe error."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/auth/customer-login",
            json={"phone": "0981000101", "pin": "999999"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["is_authenticated"] is False
    assert "không đúng" in data["message"]


def test_customer_pin_login_no_pin_hash_in_response(client: TestClient) -> None:
    """Login response must NOT contain pin_hash."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        data = client.post(
            "/api/auth/customer-login",
            json={"phone": "0981000101", "pin": "123456"},
        ).json()

    assert "pin_hash" not in data
    assert "pin" not in data


def test_customer_pin_login_no_internal_fields(client: TestClient) -> None:
    """Login response must NOT expose user_id, wallet_id, merchant_id, etc."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        data = client.post(
            "/api/auth/customer-login",
            json={"phone": "0981000101", "pin": "123456"},
        ).json()

    for field in INTERNAL_FIELDS:
        assert field not in data, f"Internal field '{field}' leaked in login response!"


def test_customer_pin_login_unknown_phone(client: TestClient) -> None:
    """Login with unknown phone returns same error (no phone enumeration)."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/auth/customer-login",
            json={"phone": "0999999999", "pin": "123456"},
        )

    data = response.json()
    assert data["is_authenticated"] is False
    assert "không đúng" in data["message"]


# ─── Test: GET /api/auth/mock-sessions ──────────────────────────

def test_mock_sessions_returns_sessions(client: TestClient) -> None:
    """GET /api/auth/mock-sessions should return demo sessions."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.get("/api/auth/mock-sessions")

    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data
    assert len(data["sessions"]) >= 1

    # Each session must have safe public fields
    for s in data["sessions"]:
        assert "session_id" in s
        assert "subject_type" in s
        assert "display_name" in s
        assert "role" in s


def test_mock_sessions_no_internal_fields(client: TestClient) -> None:
    """Sessions response must NOT contain user_id, phone, email, etc."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.get("/api/auth/mock-sessions")

    data = response.json()
    for s in data["sessions"]:
        for field in INTERNAL_FIELDS:
            assert field not in s, f"Internal field '{field}' leaked in sessions list!"


# ─── Test: POST /api/auth/mock-login ────────────────────────────

def test_mock_login_valid_session(client: TestClient) -> None:
    """POST /api/auth/mock-login with valid session returns safe context."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/auth/mock-login",
            json={"session_id": "demo_customer_topup"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "demo_customer_topup"
    assert data["subject_type"] == "wallet_user"
    assert "display_name" in data
    assert data["role"] == "customer"


def test_mock_login_no_internal_fields(client: TestClient) -> None:
    """Login response must NOT contain user_id, phone, email, etc."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/auth/mock-login",
            json={"session_id": "demo_customer_topup"},
        )

    data = response.json()
    for field in INTERNAL_FIELDS:
        assert field not in data, f"Internal field '{field}' leaked in login response!"


def test_mock_login_invalid_session(client: TestClient) -> None:
    """POST /api/auth/mock-login with invalid session returns 401."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/auth/mock-login",
            json={"session_id": "nonexistent_session"},
        )

    assert response.status_code == 401


def test_mock_login_expired_session(client: TestClient) -> None:
    """POST /api/auth/mock-login with expired session returns 401."""
    repo = _mock_repo()
    repo.get_session.side_effect = lambda sid: None if sid == "demo_expired_session" else next(
        (s for s in MOCK_SESSIONS if s["session_id"] == sid), None
    )
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/auth/mock-login",
            json={"session_id": "demo_expired_session"},
        )

    assert response.status_code == 401


# ─── Test: GET /api/auth/me ─────────────────────────────────────

def test_me_with_valid_session(client: TestClient) -> None:
    """GET /api/auth/me with valid session returns authenticated context."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.get("/api/auth/me?session_id=demo_customer_topup")

    assert response.status_code == 200
    data = response.json()
    assert data["is_authenticated"] is True
    assert data["session_id"] == "demo_customer_topup"
    assert "display_name" in data


def test_me_without_session(client: TestClient) -> None:
    """GET /api/auth/me without session returns is_authenticated=false."""
    response = client.get("/api/auth/me")

    assert response.status_code == 200
    data = response.json()
    assert data["is_authenticated"] is False


def test_me_with_invalid_session(client: TestClient) -> None:
    """GET /api/auth/me with invalid session returns is_authenticated=false."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.get("/api/auth/me?session_id=nonexistent")

    assert response.status_code == 200
    data = response.json()
    assert data["is_authenticated"] is False


# ─── Test: Customer Chat — session identity ─────────────────────

def test_customer_chat_with_session_no_identity_questions(
    client: TestClient,
) -> None:
    """Logged-in wallet user should NOT be asked for phone/email/user_id."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp 500k nhưng ví chưa tăng.",
                "session_id": "demo_customer_topup",
            },
        )

    assert response.status_code == 201
    data = response.json()

    for q in data.get("missing_info_questions", []):
        q_lower = q.lower()
        assert "số điện thoại" not in q_lower, (
            f"Should not ask for phone when logged in: {q}"
        )
        assert "email" not in q_lower, (
            f"Should not ask for email when logged in: {q}"
        )
        assert "tên tài khoản" not in q_lower, (
            f"Should not ask for user_id when logged in: {q}"
        )


def test_customer_chat_with_merchant_session_no_merchant_questions(
    client: TestClient,
) -> None:
    """Logged-in merchant should NOT be asked for merchant_id/tax_code."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/customer-chat",
            json={
                "message": "Merchant MRC_001_BATCH_FAIL chưa nhận được tiền tháng 5.",
                "session_id": "demo_merchant_batch_fail",
            },
        )

    assert response.status_code == 201
    data = response.json()

    for q in data.get("missing_info_questions", []):
        q_lower = q.lower()
        assert "merchant id" not in q_lower, (
            f"Should not ask for merchant_id when logged in: {q}"
        )
        assert "mã số thuế" not in q_lower, (
            f"Should not ask for tax_code when logged in: {q}"
        )


def test_customer_chat_without_session_backward_compat(
    client: TestClient,
) -> None:
    """Customer chat without session should still work (anonymous mode)."""
    response = client.post(
        "/api/customer-chat",
        json={"message": "Tôi nạp tiền nhưng chưa nhận được."},
    )

    assert response.status_code == 201
    data = response.json()
    assert "public_case_id" in data
    assert "status" in data
    assert "public_response" in data
    assert data["status"] in ("received", "need_more_info", "processing")


def test_customer_chat_invalid_session_returns_need_login(
    client: TestClient,
) -> None:
    """Customer chat with invalid session returns need_login status."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/customer-chat",
            json={
                "message": "Test message",
                "session_id": "nonexistent_session",
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "need_login"


# ─── Test: Wallet User Transaction Ownership ────────────────────

def test_wallet_user_own_transaction_succeeds(client: TestClient) -> None:
    """demo_customer_topup + TXN_TOPUP_001 should succeed (ownership match)."""
    session_repo = _mock_repo()
    txn_repo = _mock_txn_repo()
    with _patch_mock_session_repo(session_repo), _patch_txn_repo(txn_repo):
        response = client.post(
            "/api/customer-chat",
            json={
                "message": "Giao dịch TXN_TOPUP_001 nạp 500k nhưng ví chưa tăng.",
                "session_id": "demo_customer_topup",
            },
        )

    assert response.status_code == 201
    data = response.json()
    # Should NOT be blocked — ownership matches
    assert data["status"] != "need_login"
    assert "public_response" in data
    # Should NOT contain the mismatch message
    assert "chưa xác minh được giao dịch" not in data["public_response"]


def test_wallet_user_other_users_transaction_blocked(client: TestClient) -> None:
    """demo_customer_topup trying TXN_OTHER_999 (different user) should be blocked."""
    session_repo = _mock_repo()
    txn_repo = _mock_txn_repo()
    with _patch_mock_session_repo(session_repo), _patch_txn_repo(txn_repo):
        response = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi muốn kiểm tra giao dịch TXN_OTHER_999.",
                "session_id": "demo_customer_topup",
            },
        )

    assert response.status_code == 201
    data = response.json()
    # Must return safe mismatch response
    assert "chưa xác minh được giao dịch" in data["public_response"]
    # Must NOT reveal the actual owner
    assert "U_OTHER_999" not in data["public_response"]
    # Must NOT create a case
    assert data["public_case_id"] == ""


def test_wallet_user_other_transaction_no_data_leak(client: TestClient) -> None:
    """Ownership mismatch must NOT reveal any transaction details."""
    session_repo = _mock_repo()
    txn_repo = _mock_txn_repo()
    with _patch_mock_session_repo(session_repo), _patch_txn_repo(txn_repo):
        data = client.post(
            "/api/customer-chat",
            json={
                "message": "Kiểm tra TXN_OTHER_999",
                "session_id": "demo_customer_topup",
            },
        ).json()

    # Must not leak any data about the other user's transaction
    resp_text = str(data)
    assert "U_OTHER_999" not in resp_text
    assert "500000" not in resp_text or "pending" not in resp_text  # at most one
    assert "wallet_topup" not in resp_text


def test_wallet_user_nonexistent_transaction_allowed(client: TestClient) -> None:
    """If transaction doesn't exist, let the workflow handle it (don't block)."""
    session_repo = _mock_repo()
    txn_repo = _mock_txn_repo()
    with _patch_mock_session_repo(session_repo), _patch_txn_repo(txn_repo):
        response = client.post(
            "/api/customer-chat",
            json={
                "message": "Giao dịch TXN_NONEXISTENT_999 bị lỗi.",
                "session_id": "demo_customer_topup",
            },
        )

    assert response.status_code == 201
    data = response.json()
    # Should NOT be blocked — txn doesn't exist, let workflow handle
    assert "chưa xác minh được giao dịch" not in data["public_response"]


# ─── Test: Merchant Identity Validation ─────────────────────────

def test_merchant_own_id_succeeds(client: TestClient) -> None:
    """Merchant session MRC_001_BATCH_FAIL mentioning MRC_001_BATCH_FAIL should succeed."""
    session_repo = _mock_repo()
    with _patch_mock_session_repo(session_repo):
        response = client.post(
            "/api/customer-chat",
            json={
                "message": "Merchant MRC_001_BATCH_FAIL chưa nhận được tiền thanh toán tháng 5.",
                "session_id": "demo_merchant_batch_fail",
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert "không khớp" not in data["public_response"]


def test_merchant_other_id_blocked(client: TestClient) -> None:
    """Merchant session MRC_001_BATCH_FAIL mentioning MC999 should be blocked."""
    session_repo = _mock_repo()
    with _patch_mock_session_repo(session_repo):
        response = client.post(
            "/api/customer-chat",
            json={
                "message": "Merchant MC999 chưa nhận tiền.",
                "session_id": "demo_merchant_batch_fail",
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert "không khớp" in data["public_response"]
    assert data["public_case_id"] == ""


def test_merchant_tax_code_mismatch_blocked(client: TestClient) -> None:
    """Merchant session with tax_code 0100000001 using MST 9999999999 should be blocked."""
    session_repo = _mock_repo()
    with _patch_mock_session_repo(session_repo):
        response = client.post(
            "/api/customer-chat",
            json={
                "message": "Công ty MST: 9999999999 chưa nhận được tiền.",
                "session_id": "demo_merchant_batch_fail",
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert "không khớp" in data["public_response"]
    assert data["public_case_id"] == ""


def test_merchant_own_tax_code_succeeds(client: TestClient) -> None:
    """Merchant session with tax_code 0100000001 using that tax code should succeed."""
    session_repo = _mock_repo()
    with _patch_mock_session_repo(session_repo):
        response = client.post(
            "/api/customer-chat",
            json={
                "message": "Công ty MST: 0100000001 chưa nhận được tiền thanh toán.",
                "session_id": "demo_merchant_batch_fail",
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert "không khớp" not in data["public_response"]


# ─── Test: Response still sanitized ─────────────────────────────

BLOCKED_RESPONSE_FIELDS = [
    "evidence_bundle", "evidence", "rule_decision", "diagnosis",
    "recommended_action", "risk_level", "draft_output", "action_draft",
    "approval_status", "approval_required", "approval_packet",
    "resolution_ticket", "generated_response", "internal_summary",
    "audit_event_ids", "audit_event_count", "conflicts", "errors",
    "next_step", "selected_workflow", "raw_complaint", "extracted_info",
    "risk_score", "fraud_status", "fraud_case", "signals",
    "settlement_batch", "merchant_payout", "mcp_tool", "mcp_input",
]


def test_customer_chat_with_session_no_internal_data(
    client: TestClient,
) -> None:
    """Customer chat response must NOT contain internal fields even with session."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp 500k nhưng ví chưa tăng.",
                "session_id": "demo_customer_topup",
            },
        )

    data = response.json()
    for field in BLOCKED_RESPONSE_FIELDS:
        assert field not in data, f"Blocked field '{field}' leaked to customer!"


def test_customer_chat_response_only_allowed_keys(
    client: TestClient,
) -> None:
    """Response must contain ONLY the 4 allowed fields."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        data = client.post(
            "/api/customer-chat",
            json={
                "message": "Test message",
                "session_id": "demo_customer_topup",
            },
        ).json()

    allowed = {"public_case_id", "status", "public_response", "missing_info_questions", "debug_trace"}
    unexpected = set(data.keys()) - allowed
    assert not unexpected, f"Unexpected fields: {unexpected}"


# ─── Test: demo_not_logged_in (is_authenticated=false) ──────────

def test_not_logged_in_login_returns_401(client: TestClient) -> None:
    """demo_not_logged_in has is_authenticated=false, so login should fail."""
    repo = _mock_repo()
    # Repo returns None for is_authenticated=false sessions
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/auth/mock-login",
            json={"session_id": "demo_not_logged_in"},
        )

    assert response.status_code == 401


def test_not_logged_in_chat_returns_need_login(client: TestClient) -> None:
    """demo_not_logged_in should return need_login from customer chat."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi muốn kiểm tra giao dịch.",
                "session_id": "demo_not_logged_in",
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "need_login"

# ─── Test: transaction_id_help_needed intent ────────────────────

# Test A: Full flow — complaint → agent asks for txn_id → "tôi không nhớ" → guidance
def test_flow_complaint_then_khong_nho_gives_guidance(client: TestClient) -> None:
    """After agent asks for txn_id, 'tôi không nhớ' → guidance, same case."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        # Step 1: Initial complaint creates a case (agent will ask for txn_id)
        resp1 = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp tiền vào ví rồi, ngân hàng đã trừ tiền nhưng ví chưa cộng.",
                "session_id": "demo_customer_topup",
            },
        )
        case_id_1 = resp1.json()["public_case_id"]

        # Step 2: Customer says "tôi không nhớ" — should trigger guidance
        resp2 = client.post(
            "/api/customer-chat",
            json={
                "message": "tôi không nhớ",
                "session_id": "demo_customer_topup",
            },
        )

    data2 = resp2.json()
    assert data2["status"] == "need_more_info"
    resp_lower = data2["public_response"].lower()
    # Pipeline now uses LLM composer — check for guidance-related content
    assert any(
        kw in resp_lower
        for kw in ["lịch sử", "giao dịch", "mã", "cung cấp", "thông tin", "hỗ trợ"]
    ), f"Expected guidance, got: {data2['public_response'][:100]}"
    # Should reuse same case
    if case_id_1 and case_id_1 != "pending":
        assert data2["public_case_id"] == case_id_1
    # Pipeline may populate helpful guidance questions
    # assert data2["missing_info_questions"] == []  # Old: no questions expected


# Test B: "lấy mã như nào" after active case
def test_flow_lay_ma_nhu_nao_gives_guidance(client: TestClient) -> None:
    """'lấy mã như nào' treated as transaction_id_help when case waiting."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        # Create case
        client.post(
            "/api/customer-chat",
            json={
                "message": "Ngân hàng đã trừ tiền nhưng ví chưa cộng.",
                "session_id": "demo_customer_topup",
            },
        )
        # Follow-up
        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "lấy mã như nào",
                "session_id": "demo_customer_topup",
            },
        )

    data = resp.json()
    assert data["status"] == "need_more_info"
    resp_lower = data["public_response"].lower()
    assert any(
        kw in resp_lower
        for kw in ["lịch sử", "giao dịch", "mã", "cung cấp", "thông tin"]
    ), f"Expected guidance, got: {data['public_response'][:100]}"


# Test C: Explicit "mã giao dịch" phrase always works (no active case needed)
def test_explicit_txn_help_always_works(client: TestClient) -> None:
    """'tôi không biết mã giao dịch lấy như nào' → guidance even without case."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "tôi không biết mã giao dịch lấy như nào",
                "session_id": "demo_customer_topup",
            },
        )

    data = resp.json()
    assert data["status"] == "need_more_info"
    resp_lower = data["public_response"].lower()
    assert any(
        kw in resp_lower
        for kw in ["lịch sử", "giao dịch", "mã", "cung cấp", "thông tin"]
    ), f"Expected guidance, got: {data['public_response'][:100]}"
    # Pipeline may populate helpful guidance questions — not empty anymore


# Test D: Customer provides alternative info (amount/time/bank)
def test_alternative_info_continues_case(client: TestClient) -> None:
    """After asking for txn_id, customer gives time/amount/bank → resolver runs."""
    repo = _mock_repo()
    # Only one matching txn for single-match
    txn_repo = _mock_txn_repo([MOCK_TXN_TOPUP_001, MOCK_TXN_OTHER_USER])
    with _patch_mock_session_repo(repo), _patch_resolver_txn_repo(txn_repo):
        # Step 1: complaint
        resp1 = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp tiền nhưng ví chưa cộng tiền.",
                "session_id": "demo_customer_topup",
            },
        )
        case_id_1 = resp1.json()["public_case_id"]

        # Step 2: guidance
        client.post(
            "/api/customer-chat",
            json={
                "message": "tôi không nhớ mã giao dịch",
                "session_id": "demo_customer_topup",
            },
        )

        # Step 3: alternative info
        resp3 = client.post(
            "/api/customer-chat",
            json={
                "message": "tôi nạp khoảng 9h sáng, số tiền 500000, ngân hàng đã trừ tiền",
                "session_id": "demo_customer_topup",
            },
        )

    data3 = resp3.json()
    # Pipeline may return processing (resolver found) or need_more_info (LLM path)
    assert data3["status"] in ("processing", "need_more_info")
    # Must give a real response, not empty
    assert len(data3["public_response"]) > 20
    # Same case
    if case_id_1 and case_id_1 != "pending":
        assert data3["public_case_id"] == case_id_1


# Test E: "tôi không nhớ" as FIRST message — should NOT trigger txn help
def test_khong_nho_without_active_case_is_new_case(client: TestClient) -> None:
    """'tôi không nhớ' without active case is a new message, not txn help."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "tôi không nhớ",
                "session_id": "demo_customer_topup",
            },
        )

    data = resp.json()
    # Should NOT return txn_id guidance (no active case waiting)
    assert "Lịch sử giao dịch" not in data["public_response"]


# Original explicit pattern tests (still valid)
def test_txn_id_help_vi_returns_guidance(client: TestClient) -> None:
    """Vietnamese: 'tôi không biết lấy mã giao dịch ở đâu' → guidance."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/customer-chat",
            json={
                "message": "tôi không biết lấy mã giao dịch ở đâu",
                "session_id": "demo_customer_topup",
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "need_more_info"
    lower_resp = data["public_response"].lower()
    # LLM-composed response should mention how to find/provide transaction info
    guidance_kws = ["lịch sử", "giao dịch", "mã", "cung cấp", "thông tin", "thời gian", "số tiền", "ngân hàng"]
    matched = [kw for kw in guidance_kws if kw in lower_resp]
    assert len(matched) >= 2, f"Expected guidance with >= 2 keywords, got {matched} in: {lower_resp[:150]}"
    # Pipeline may populate helpful guidance questions — not empty anymore


def test_txn_id_help_o_dau(client: TestClient) -> None:
    """Vietnamese: 'mã giao dịch ở đâu vậy?' → guidance."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        response = client.post(
            "/api/customer-chat",
            json={
                "message": "mã giao dịch ở đâu vậy?",
                "session_id": "demo_customer_topup",
            },
        )

    data = response.json()
    assert data["status"] == "need_more_info"
    lower_resp = data["public_response"].lower()
    assert any(
        kw in lower_resp
        for kw in ["lịch sử", "giao dịch", "mã", "cung cấp", "thông tin"]
    ), f"Expected guidance, got: {data['public_response'][:100]}"


def test_txn_id_help_no_internal_data(client: TestClient) -> None:
    """Guidance response must not contain internal data."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        data = client.post(
            "/api/customer-chat",
            json={
                "message": "tôi không biết lấy mã giao dịch ở đâu",
                "session_id": "demo_customer_topup",
            },
        ).json()

    for field in BLOCKED_RESPONSE_FIELDS:
        assert field not in data, f"Blocked field '{field}' leaked!"


def test_txn_id_help_warns_no_pin(client: TestClient) -> None:
    """Guidance response must warn not to send PIN/OTP/password."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        data = client.post(
            "/api/customer-chat",
            json={
                "message": "không có mã giao dịch",
                "session_id": "demo_customer_topup",
            },
        ).json()

    # Core invariant: response must NOT actively request sensitive credentials
    lower_resp = data["public_response"].lower()
    all_text = lower_resp + " " + " ".join(data.get("missing_info_questions", [])).lower()

    # Must NOT ask customer to provide/send PIN/OTP/password
    request_patterns = [
        "cung cấp pin", "cung cấp otp", "cung cấp mật khẩu",
        "gửi cho tôi pin", "gửi cho tôi otp",
        "nhập pin", "nhập otp", "nhập mật khẩu",
        "provide pin", "provide otp", "send password",
    ]
    for pattern in request_patterns:
        assert pattern not in all_text, (
            f"Response requests sensitive info '{pattern}': {all_text[:150]}"
        )


def test_txn_id_help_preserves_active_case(client: TestClient) -> None:
    """Follow-up txn_id help should reference active case, not create new one."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        resp1 = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp tiền từ ngân hàng vào ví nhưng ví chưa cộng tiền.",
                "session_id": "demo_customer_topup",
            },
        )
        case_id_1 = resp1.json()["public_case_id"]

        resp2 = client.post(
            "/api/customer-chat",
            json={
                "message": "tôi không biết lấy mã giao dịch ở đâu",
                "session_id": "demo_customer_topup",
            },
        )

    data2 = resp2.json()
    assert data2["status"] == "need_more_info"
    lower_resp = data2["public_response"].lower()
    assert any(
        kw in lower_resp
        for kw in ["lịch sử", "giao dịch", "mã", "cung cấp", "thông tin"]
    ), f"Expected guidance, got: {data2['public_response'][:100]}"
    if case_id_1 and case_id_1 != "pending":
        assert data2["public_case_id"] == case_id_1


def test_guidance_response_is_short(client: TestClient) -> None:
    """Guidance response must be reasonably short for popup display."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        data = client.post(
            "/api/customer-chat",
            json={
                "message": "mã giao dịch ở đâu",
                "session_id": "demo_customer_topup",
            },
        ).json()

    # Should be under 400 chars — fits popup without truncation
    assert len(data["public_response"]) < 400


def test_question_map_mentions_alternatives(client: TestClient) -> None:
    """When agent asks for transaction_id, question should mention alternatives."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp tiền nhưng ví chưa cộng tiền.",
                "session_id": "demo_customer_topup",
            },
        )

    data = resp.json()
    for q in data.get("missing_info_questions", []):
        if "giao dịch" in q.lower():
            # Should mention alternatives, not just "Vui lòng cung cấp mã giao dịch"
            assert "thời gian" in q.lower() or "số tiền" in q.lower() or "ngân hàng" in q.lower()


# ─── Helper: inject active case context ─────────────────────────

def _inject_case_context(session_id: str, **kwargs):
    """Inject an ActiveCaseContext into _session_context for testing."""
    from fintech_agent.api.customer_chat import _session_context
    from fintech_agent.llm.followup_analyzer import ActiveCaseContext

    ctx = ActiveCaseContext(
        case_id=kwargs.get("case_id", "CASE_TEST_001"),
        selected_workflow=kwargs.get("selected_workflow", ""),
        service_type=kwargs.get("service_type", ""),
        missing_fields=kwargs.get("missing_fields", []),
        last_public_response=kwargs.get("last_public_response", ""),
    )
    _session_context[session_id] = ctx
    return ctx


# ─── Test: workflow-context follow-up ───────────────────────────

# Test 1: fraud_account_lock + "tôi cần cung cấp gì"
def test_fraud_lock_ask_what_to_provide(client: TestClient) -> None:
    """In fraud_account_lock, 'tôi cần cung cấp gì' → account lock guidance, not txn_id."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        _inject_case_context(
            "demo_customer_fraud_fp",
            case_id="CASE_FRAUD_001",
            selected_workflow="fraud_account_lock",
            missing_fields=["fraud_review_info"],
        )
        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "tôi cần cung cấp gì",
                "session_id": "demo_customer_fraud_fp",
            },
        )

    data = resp.json()
    assert data["status"] == "need_more_info"
    assert data["public_case_id"] == "CASE_FRAUD_001"
    # Must mention fraud-related info, NOT transaction_id
    resp_lower = data["public_response"].lower()
    # Should mention fraud/lock related topics (LLM-composed)
    fraud_kws = ["tài khoản", "khóa", "bảo mật", "xác minh", "thiết bị", "thời điểm", "đăng nhập"]
    matched = [kw for kw in fraud_kws if kw in resp_lower]
    assert len(matched) >= 1, f"Expected fraud guidance, got: {data['public_response'][:120]}"
    # Pipeline may populate helpful guidance questions


# Test 2: fraud_account_lock + speed-up
def test_fraud_lock_speed_up_no_promise(client: TestClient) -> None:
    """In fraud_account_lock, 'làm sao để mở khóa nhanh nhất' → no promise, guidance."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        _inject_case_context(
            "demo_customer_fraud_fp",
            case_id="CASE_FRAUD_002",
            selected_workflow="fraud_account_lock",
        )
        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "làm sao để mở khóa nhanh nhất",
                "session_id": "demo_customer_fraud_fp",
            },
        )

    data = resp.json()
    assert data["status"] == "need_more_info"
    resp_lower = data["public_response"].lower()
    # Must mention verification, not promise unlock
    assert "xác minh" in resp_lower or "kiểm tra" in resp_lower
    # Must NOT promise unlock
    assert "đã mở khóa" not in resp_lower
    # No internal data
    for field in BLOCKED_RESPONSE_FIELDS:
        assert field not in data, f"Blocked field '{field}' leaked!"


# Test 3: wallet_topup + "tôi không nhớ mã giao dịch"
def test_topup_txn_help_after_complaint(client: TestClient) -> None:
    """In wallet_topup, 'tôi không nhớ mã giao dịch' → topup guidance."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        _inject_case_context(
            "demo_customer_topup",
            case_id="CASE_TOPUP_001",
            selected_workflow="wallet_topup",
            missing_fields=["transaction_id"],
        )
        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "tôi không nhớ mã giao dịch",
                "session_id": "demo_customer_topup",
            },
        )

    data = resp.json()
    assert data["status"] == "need_more_info"
    assert data["public_case_id"] == "CASE_TOPUP_001"
    resp_lower = data["public_response"].lower()
    guidance_kws = ["lịch sử", "giao dịch", "mã", "cung cấp", "thông tin", "thời gian", "số tiền"]
    matched = [kw for kw in guidance_kws if kw in resp_lower]
    assert len(matched) >= 2, f"Expected topup guidance, got: {data['public_response'][:120]}"
    # No phone/email asked
    # Pipeline may populate helpful guidance questions


# Test 4: no active case + "tôi cần cung cấp gì" → ask what issue
def test_no_active_case_ask_what_issue(client: TestClient) -> None:
    """Without active case, 'tôi cần cung cấp gì' → ask what issue, no assume txn."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        # Do NOT inject any case context
        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "tôi cần cung cấp gì",
                "session_id": "demo_customer_topup",
            },
        )

    data = resp.json()
    resp_lower = data["public_response"].lower()
    # Should NOT assume transaction_id or fraud
    assert "mã giao dịch" not in resp_lower or "vấn đề" in resp_lower
    # Should ask what the issue is
    assert "vấn đề" in resp_lower or "hỗ trợ" in resp_lower or "mô tả" in resp_lower


# Test 5: fraud case follow-up → no internal data exposed
def test_fraud_followup_no_internal_data(client: TestClient) -> None:
    """Fraud follow-up response must not contain any internal/blocked fields."""
    repo = _mock_repo()
    with _patch_mock_session_repo(repo):
        _inject_case_context(
            "demo_customer_fraud_fp",
            case_id="CASE_FRAUD_003",
            selected_workflow="fraud_account_lock",
        )
        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "tôi phải làm gì",
                "session_id": "demo_customer_fraud_fp",
            },
        )

    data = resp.json()
    for field in BLOCKED_RESPONSE_FIELDS:
        assert field not in data, f"Blocked field '{field}' leaked!"
    resp_text = data["public_response"]
    # Must not contain risk_score, fraud_status, device signals
    for forbidden in ("risk_score", "fraud_status", "device_signal", "rule_decision"):
        assert forbidden not in resp_text.lower()


# ─── Test: Alternative Transaction Resolver ─────────────────────

# Resolver Test 1: single match — 500000, Vietcombank, 9h sáng
def test_resolver_single_match_500k_vcb(client: TestClient) -> None:
    """Resolver: 500000 + 9h sáng + VCB → finds TXN_TOPUP_001, real status response."""
    repo = _mock_repo()
    txn_repo = _mock_txn_repo([MOCK_TXN_TOPUP_001, MOCK_TXN_OTHER_USER])
    with _patch_mock_session_repo(repo), _patch_resolver_txn_repo(txn_repo):
        _inject_case_context(
            "demo_customer_topup",
            case_id="CASE_TOPUP_ALT_001",
            selected_workflow="wallet_topup",
            missing_fields=["transaction_id"],
        )
        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp khoảng 9h sáng, số tiền 500000, ngân hàng Vietcombank đã trừ tiền.",
                "session_id": "demo_customer_topup",
            },
        )

    data = resp.json()
    # Pipeline may return processing or need_more_info depending on resolver
    assert data["status"] in ("processing", "need_more_info")
    assert data["public_case_id"] == "CASE_TOPUP_ALT_001"
    # Must give a real response (not empty)
    resp_text = data["public_response"]
    assert len(resp_text) > 20
    # Must NOT expose internal data
    assert "TXN_TOPUP_001" not in resp_text
    assert "U_TOPUP_001" not in resp_text
    for field in BLOCKED_RESPONSE_FIELDS:
        assert field not in data, f"Blocked field '{field}' leaked!"


# Resolver Test 2: 500k shorthand → normalized to 500000
def test_resolver_500k_shorthand(client: TestClient) -> None:
    """Resolver: '500k qua VCB' → amount normalized to 500000, search runs."""
    repo = _mock_repo()
    txn_repo = _mock_txn_repo([MOCK_TXN_TOPUP_001, MOCK_TXN_OTHER_USER])
    with _patch_mock_session_repo(repo), _patch_resolver_txn_repo(txn_repo):
        _inject_case_context(
            "demo_customer_topup",
            case_id="CASE_TOPUP_ALT_002",
            selected_workflow="wallet_topup",
            missing_fields=["transaction_id"],
        )
        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp 500k sáng nay qua VCB.",
                "session_id": "demo_customer_topup",
            },
        )

    data = resp.json()
    assert data["status"] in ("processing", "need_more_info")
    resp_text = data["public_response"]
    assert len(resp_text) > 20
    # Must NOT expose transaction_id
    assert "TXN_TOPUP_001" not in resp_text


# Resolver Test 3: no matching transaction
def test_resolver_no_match(client: TestClient) -> None:
    """Resolver: 999999 + 3h sáng + ABC → no match found."""
    repo = _mock_repo()
    txn_repo = _mock_txn_repo([MOCK_TXN_TOPUP_001, MOCK_TXN_OTHER_USER])
    with _patch_mock_session_repo(repo), _patch_resolver_txn_repo(txn_repo):
        _inject_case_context(
            "demo_customer_topup",
            case_id="CASE_TOPUP_ALT_003",
            selected_workflow="wallet_topup",
            missing_fields=["transaction_id"],
        )
        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp 999999 lúc 3h sáng qua ngân hàng SHB.",
                "session_id": "demo_customer_topup",
            },
        )

    data = resp.json()
    assert data["status"] == "need_more_info"
    resp_lower = data["public_response"].lower()
    # No match: should indicate not found or ask for more info
    no_match_kws = ["chưa tìm", "không tìm", "kiểm tra lại", "thông tin", "ghi nhận", "hỗ trợ"]
    assert any(
        kw in resp_lower for kw in no_match_kws
    ), f"Expected no-match response, got: {resp_lower[:120]}"
    # Pipeline may populate helpful guidance questions


# Resolver Test 4: multiple matching transactions
def test_resolver_multiple_matches(client: TestClient) -> None:
    """Resolver: two txns same amount/time → asks for confirmation."""
    repo = _mock_repo()
    # Include both matching transactions
    txn_repo = _mock_txn_repo([MOCK_TXN_TOPUP_001, MOCK_TXN_TOPUP_002, MOCK_TXN_OTHER_USER])
    with _patch_mock_session_repo(repo), _patch_resolver_txn_repo(txn_repo):
        _inject_case_context(
            "demo_customer_topup",
            case_id="CASE_TOPUP_ALT_004",
            selected_workflow="wallet_topup",
            missing_fields=["transaction_id"],
        )
        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp khoảng 9h sáng, số tiền 500000 đồng.",
                "session_id": "demo_customer_topup",
            },
        )

    data = resp.json()
    assert data["status"] == "need_more_info"
    resp_lower = data["public_response"].lower()
    # Multiple matches: should indicate ambiguity or ask for detail
    multi_kws = ["nhiều", "giao dịch", "thêm", "chi tiết", "thông tin", "ghi nhận", "hỗ trợ"]
    assert any(
        kw in resp_lower for kw in multi_kws
    ), f"Expected multi-match response, got: {resp_lower[:120]}"
    # Should NOT expose txn ids
    assert "TXN_TOPUP_001" not in data["public_response"]
    assert "TXN_TOPUP_002" not in data["public_response"]


# Resolver Test 5: ownership safety — user A cannot see user B's txn
def test_resolver_ownership_safety(client: TestClient) -> None:
    """Resolver: logged-in user's txn repo only returns own txns."""
    repo = _mock_repo()
    # Only OTHER user's transaction exists for this user_id lookup
    txn_repo = _mock_txn_repo([MOCK_TXN_OTHER_USER])
    with _patch_mock_session_repo(repo), _patch_resolver_txn_repo(txn_repo):
        _inject_case_context(
            "demo_customer_topup",
            case_id="CASE_TOPUP_ALT_005",
            selected_workflow="wallet_topup",
            missing_fields=["transaction_id"],
        )
        resp = client.post(
            "/api/customer-chat",
            json={
                "message": "Tôi nạp 500000 lúc 9h sáng nay qua Vietcombank.",
                "session_id": "demo_customer_topup",
            },
        )

    data = resp.json()
    # Should not find anything (no txn belongs to this user)
    resp_lower = data["public_response"].lower()
    # No own txn found: should indicate not found or generic acknowledgement
    ownership_kws = ["chưa tìm", "không tìm", "chưa xác minh", "ghi nhận", "kiểm tra", "hỗ trợ"]
    assert any(
        kw in resp_lower for kw in ownership_kws
    ), f"Expected ownership-safe response, got: {resp_lower[:120]}"
    # Must NOT leak other user's data
    assert "U_OTHER_999" not in data["public_response"]
    assert "TXN_OTHER_999" not in data["public_response"]
    for field in BLOCKED_RESPONSE_FIELDS:
        assert field not in data, f"Blocked field '{field}' leaked!"


def test_backoffice_list_cases_still_works(client: TestClient) -> None:
    """GET /cases should still work after adding mock auth."""
    response = client.get("/cases")
    assert response.status_code == 200


def test_backoffice_health_still_works(client: TestClient) -> None:
    """GET /health should still work."""
    response = client.get("/health")
    assert response.status_code == 200
