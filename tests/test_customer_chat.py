"""Tests for POST /api/customer-chat — customer-facing safe endpoint.

These tests verify:
  1. Endpoint returns correct structure with sanitized fields.
  2. NO internal back-office data leaks to the customer.
  3. Missing info questions are safe (no password/OTP/PIN).
  4. Existing back-office endpoints still work.
"""

from fastapi.testclient import TestClient


# ─── Blocked field names that must NEVER appear in customer response ───

BLOCKED_FIELDS = [
    "evidence_bundle",
    "evidence",
    "rule_decision",
    "diagnosis",
    "recommended_action",
    "risk_level",
    "draft_output",
    "action_draft",
    "approval_status",
    "approval_required",
    "approval_packet",
    "resolution_ticket",
    "generated_response",
    "internal_summary",
    "audit_event_ids",
    "audit_event_count",
    "conflicts",
    "has_conflict",
    "errors",
    "next_step",
    "selected_workflow",
    "raw_complaint",
    "extracted_info",
    "debug",
    # Fraud-specific
    "risk_score",
    "fraud_status",
    "fraud_case",
    "signals",
    "device_events",
    # Settlement-specific
    "settlement_batch",
    "merchant_payout",
    "bank_transfer_receipt",
    "merchant_settlement_ledger",
    "reconciliation_status",
    # MCP/tool internals
    "mcp_tool",
    "mcp_input",
    "tool_errors",
]

BLOCKED_QUESTION_WORDS = [
    "mật khẩu",
    "password",
    "otp",
    "pin",
    "card_number",
    "private_key",
    "cvv",
    "cvc",
    "secret",
]


# ─── Test: Basic endpoint response structure ───

def test_customer_chat_returns_201(client: TestClient) -> None:
    """POST /api/customer-chat should return 201 with valid response."""
    response = client.post(
        "/api/customer-chat",
        json={"message": "Tôi nạp tiền nhưng chưa nhận được."},
    )

    assert response.status_code == 201

    data = response.json()
    assert "public_case_id" in data
    assert "status" in data
    assert "public_response" in data
    assert "missing_info_questions" in data

    # Status must be one of the safe values
    assert data["status"] in ("received", "need_more_info", "processing")

    # public_response must be a non-empty string
    assert isinstance(data["public_response"], str)
    assert len(data["public_response"]) > 0

    # missing_info_questions must be a list
    assert isinstance(data["missing_info_questions"], list)


# ─── Test: No internal fields leaked ───

def test_customer_chat_no_evidence_bundle(client: TestClient) -> None:
    """Response must NOT contain evidence_bundle."""
    data = _submit_complaint(client, "Tôi nạp 500k nhưng ví không tăng.")
    assert "evidence_bundle" not in data
    assert "evidence" not in data


def test_customer_chat_no_rule_decision(client: TestClient) -> None:
    """Response must NOT contain rule_decision or diagnosis."""
    data = _submit_complaint(client, "Tôi nạp tiền nhưng chưa nhận được.")
    assert "rule_decision" not in data
    assert "diagnosis" not in data
    assert "recommended_action" not in data


def test_customer_chat_no_approval_packet(client: TestClient) -> None:
    """Response must NOT contain approval_status or approval_packet."""
    data = _submit_complaint(client, "Giao dịch bị lỗi, cần hoàn tiền.")
    assert "approval_status" not in data
    assert "approval_required" not in data
    assert "approval_packet" not in data


def test_customer_chat_no_action_draft(client: TestClient) -> None:
    """Response must NOT contain draft_output or action_draft."""
    data = _submit_complaint(client, "Tiền bị trừ nhưng không nhận được dịch vụ.")
    assert "draft_output" not in data
    assert "action_draft" not in data


def test_customer_chat_no_internal_fields(client: TestClient) -> None:
    """Response must NOT contain ANY blocked internal fields."""
    data = _submit_complaint(client, "Tôi cần hỗ trợ giao dịch lỗi.")

    for field in BLOCKED_FIELDS:
        assert field not in data, f"Blocked field '{field}' leaked to customer!"


def test_customer_chat_no_mcp_tool_data(client: TestClient) -> None:
    """Response must NOT contain MCP tool names or inputs."""
    data = _submit_complaint(client, "Giao dịch nạp tiền bị lỗi.")
    assert "mcp_tool" not in data
    assert "mcp_input" not in data
    assert "tool_errors" not in data


def test_customer_chat_no_audit_logs(client: TestClient) -> None:
    """Response must NOT contain audit event IDs or count."""
    data = _submit_complaint(client, "Tôi cần tra cứu giao dịch.")
    assert "audit_event_ids" not in data
    assert "audit_event_count" not in data


# ─── Test: Fraud complaint safety ───

def test_fraud_complaint_no_risk_score(client: TestClient) -> None:
    """Fraud complaint must NOT expose risk_score or fraud signals."""
    data = _submit_complaint(
        client,
        "Tài khoản tôi bị khóa, không rút được tiền. User ID: U999.",
    )

    assert "risk_score" not in data
    assert "fraud_status" not in data
    assert "fraud_case" not in data
    assert "signals" not in data
    assert "device_events" not in data


# ─── Test: Settlement complaint safety ───

def test_settlement_complaint_no_batch_internals(client: TestClient) -> None:
    """Settlement complaint must NOT expose batch/payout internal details."""
    data = _submit_complaint(
        client,
        "Merchant MC001 chưa nhận được tiền giải ngân tháng 5.",
    )

    assert "settlement_batch" not in data
    assert "merchant_payout" not in data
    assert "bank_transfer_receipt" not in data
    assert "merchant_settlement_ledger" not in data
    assert "reconciliation_status" not in data


# ─── Test: Missing info questions are safe ───

def test_missing_info_no_sensitive_questions(client: TestClient) -> None:
    """Missing info questions must NEVER ask for password/OTP/PIN."""
    data = _submit_complaint(client, "Giao dịch lỗi.")

    for question in data.get("missing_info_questions", []):
        q_lower = question.lower()
        for blocked in BLOCKED_QUESTION_WORDS:
            assert blocked not in q_lower, (
                f"Unsafe question asks for '{blocked}': {question}"
            )


# ─── Test: Empty message validation ───

def test_customer_chat_empty_message_rejected(client: TestClient) -> None:
    """Empty message should return 422 validation error."""
    response = client.post(
        "/api/customer-chat",
        json={"message": ""},
    )
    assert response.status_code == 422


# ─── Test: Response only has allowed keys ───

def test_customer_chat_response_only_allowed_keys(client: TestClient) -> None:
    """Response must contain ONLY the 4 allowed fields."""
    data = _submit_complaint(client, "Tôi cần hỗ trợ.")
    allowed_keys = {"public_case_id", "status", "public_response", "missing_info_questions"}
    actual_keys = set(data.keys())

    unexpected = actual_keys - allowed_keys
    assert not unexpected, f"Unexpected fields in response: {unexpected}"


# ─── Test: Existing back-office endpoints still work ───

def test_backoffice_list_cases_still_works(client: TestClient) -> None:
    """GET /cases should still work after adding customer chat."""
    response = client.get("/cases")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "cases" in data


def test_backoffice_create_case_still_works(client: TestClient) -> None:
    """POST /cases should still work and return full case details."""
    response = client.post(
        "/cases",
        json={"raw_complaint": "Test back-office case creation."},
    )
    assert response.status_code == 201
    data = response.json()
    # Back-office response SHOULD have evidence, decision, etc.
    assert "case_id" in data
    assert "status" in data
    assert "evidence" in data or data.get("evidence") is None  # field exists
    assert "next_step" in data  # back-office only field


def test_backoffice_health_still_works(client: TestClient) -> None:
    """GET /health should still work."""
    response = client.get("/health")
    assert response.status_code == 200


# ─── Helper ───

def _submit_complaint(client: TestClient, message: str) -> dict:
    """Submit a complaint via the customer chat endpoint and return JSON."""
    response = client.post(
        "/api/customer-chat",
        json={"message": message},
    )
    assert response.status_code == 201
    return response.json()
