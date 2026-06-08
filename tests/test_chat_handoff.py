"""Customer-chat → back-office handoff ticket tests.

Covers: one-ticket-per-chat (dedup), complainant mapping, redaction of
sensitive data, status mapping, and dashboard filters/search.

Logic-level + TestClient for the back-office API. No LLM/Supabase calls.
"""

import pytest
from fastapi.testclient import TestClient

from fintech_agent.main import create_app
from fintech_agent.api.chat_handoff import (
    finalize_customer_chat_and_handoff,
    get_ticket_store,
    reset_ticket_store,
)
from fintech_agent.schemas.chat_handoff import (
    SOURCE_CUSTOMER_CHAT,
    TICKET_NEED_MORE_INFO,
    TICKET_PENDING_APPROVAL,
)
from fintech_agent.llm.message_analyzer import ActiveCaseContext


@pytest.fixture(autouse=True)
def _clean_store():
    reset_ticket_store()
    yield
    reset_ticket_store()


@pytest.fixture
def client():
    return TestClient(create_app())


def _wallet_session():
    return {
        "session_id": "sess_wallet", "subject_type": "wallet_user",
        "display_name": "Nguyễn Văn A", "phone": "0981000101",
        "email": "a@example.com", "user_id": "U_TEST", "wallet_id": "W_TEST",
        "account_status": "active", "wallet_status": "active",
    }


def _merchant_session():
    return {
        "session_id": "sess_merchant", "subject_type": "merchant",
        "display_name": "Shop Demo", "merchant_name": "Shop Demo",
        "phone": "0903000001", "merchant_id": "MC_TEST", "tax_code": "TAX_TEST",
        "bank_account_status": "verified", "settlement_cycle": "D+1",
    }


def _wallet_ctx():
    ctx = ActiveCaseContext(
        case_id="CASE_W", selected_workflow="wallet_topup",
        subject_type="wallet_user",
    )
    ctx.customer_problem = "Tôi nạp tiền nhưng chưa vào ví"
    ctx.transcript = [
        {"role": "customer", "text": "Tôi nạp tiền nhưng chưa vào ví", "timestamp": "t1"},
        {"role": "agent", "text": "Chúng tôi đang kiểm tra giao dịch nạp tiền.", "timestamp": "t2"},
    ]
    ctx.last_diagnosis = {
        "workflow": "wallet_topup", "situation": "payment_ok_delivery_pending",
        "customer_safe_cause": "Ngân hàng đã xác nhận nhưng ví chưa cập nhật.",
        "confirmed_public_facts": ["ngân hàng đã xác nhận thanh toán"],
        "what_was_checked": ["trạng thái phía ngân hàng"], "confidence": "medium",
    }
    return ctx


def _merchant_ctx():
    ctx = ActiveCaseContext(
        case_id="CASE_M", selected_workflow="merchant_settlement_delay",
        subject_type="merchant",
    )
    ctx.customer_problem = "Quá chu kỳ D+1 chưa nhận tiền giải ngân"
    ctx.transcript = [
        {"role": "customer", "text": "Quá chu kỳ D+1 chưa nhận tiền giải ngân", "timestamp": "t1"},
    ]
    ctx.last_diagnosis = {
        "workflow": "merchant_settlement_delay", "situation": "payout_incomplete",
        "customer_safe_cause": "Khoản giải ngân chưa hoàn tất.",
        "confidence": "medium",
    }
    return ctx


# ─── Test 1: Wallet user unresolved topup ──────────────────────

def test_wallet_topup_handoff_creates_one_ticket():
    t = finalize_customer_chat_and_handoff(
        _wallet_session(), _wallet_ctx(), reason="expired",
        needs_more_info=True,
        case_state={"rule_decision": {"action": "create_reconciliation_ticket_draft"},
                    "approval_required": False, "risk_level": "low"},
    )
    assert len(get_ticket_store().list_all()) == 1
    assert t.source == SOURCE_CUSTOMER_CHAT
    assert t.selected_workflow == "wallet_topup"
    # complainant fields present for staff
    assert t.complainant.display_name == "Nguyễn Văn A"
    assert t.complainant.phone == "0981000101"
    assert t.complainant.user_id == "U_TEST"
    assert t.complainant.wallet_id == "W_TEST"
    # recommended action visible to staff
    assert t.recommended_action == "create_reconciliation_ticket_draft"


# ─── Test 2: Merchant settlement ───────────────────────────────

def test_merchant_settlement_handoff_review_draft_only():
    t = finalize_customer_chat_and_handoff(
        _merchant_session(), _merchant_ctx(), reason="ended",
        case_state={"rule_decision": {"action": "create_manual_payout_draft"},
                    "approval_required": True, "risk_level": "medium"},
    )
    assert t.selected_workflow == "merchant_settlement_delay"
    assert t.complainant.merchant_id == "MC_TEST"
    assert t.complainant.tax_code == "TAX_TEST"
    assert t.complainant.display_name == "Shop Demo"
    # action is review/draft only and gated by approval (never auto-executed)
    assert "draft" in t.recommended_action
    assert t.approval_required is True
    assert t.backoffice_ticket_status == TICKET_PENDING_APPROVAL


# ─── Test 3: Customer sends PIN/OTP → redacted ─────────────────

def test_sensitive_info_redacted_in_ticket():
    ctx = _wallet_ctx()
    ctx.transcript.append(
        {"role": "customer", "text": "PIN của tôi là 123456, OTP 778899", "timestamp": "t3"},
    )
    t = finalize_customer_chat_and_handoff(_wallet_session(), ctx, reason="ended")
    blob = " ".join([t.conversation_summary, t.latest_customer_message,
                     *[m.text for m in t.timeline]])
    assert "123456" not in blob
    assert "778899" not in blob
    # safe complaint info still present
    assert t.complainant.display_name == "Nguyễn Văn A"
    assert t.selected_workflow == "wallet_topup"


# ─── Test 4: Reopen within TTL → no ticket / no duplicate ──────

def test_no_finalize_means_no_ticket():
    # A quick close+reopen does NOT call finalize → no ticket exists.
    assert len(get_ticket_store().list_all()) == 0


def test_finalize_twice_is_deduped():
    s, ctx = _wallet_session(), _wallet_ctx()
    t1 = finalize_customer_chat_and_handoff(s, ctx, reason="ended")
    t2 = finalize_customer_chat_and_handoff(s, ctx, reason="expired")
    assert t1.ticket_id == t2.ticket_id
    assert len(get_ticket_store().list_all()) == 1
    assert get_ticket_store().get(t1.ticket_id).handoff_reason == "expired"


# ─── Test 5: TTL expiry → ticket created ───────────────────────

def test_ttl_expiry_creates_ticket():
    t = finalize_customer_chat_and_handoff(
        _wallet_session(), _wallet_ctx(), reason="expired", needs_more_info=True,
    )
    assert t.handoff_reason == "expired"
    assert t.backoffice_ticket_status == TICKET_NEED_MORE_INFO
    assert len(get_ticket_store().list_all()) == 1


# ─── Test 6: Dashboard filters + search ────────────────────────

def test_dashboard_filters_and_search(client):
    finalize_customer_chat_and_handoff(
        _wallet_session(), _wallet_ctx(), reason="ended",
        case_state={"rule_decision": {"action": "create_reconciliation_ticket_draft"},
                    "approval_required": False, "risk_level": "low"},
    )
    finalize_customer_chat_and_handoff(
        _merchant_session(), _merchant_ctx(), reason="ended",
        case_state={"rule_decision": {"action": "create_manual_payout_draft"},
                    "approval_required": True, "risk_level": "medium"},
    )

    # Source filter = customer_chat returns both
    r = client.get("/api/backoffice/chat-tickets", params={"source": "customer_chat"})
    assert r.status_code == 200
    assert r.json()["total"] == 2

    # Filter by subject_type
    r = client.get("/api/backoffice/chat-tickets", params={"subject_type": "merchant"})
    assert r.json()["total"] == 1
    assert r.json()["tickets"][0]["selected_workflow"] == "merchant_settlement_delay"

    # Filter by workflow + approval_required
    r = client.get("/api/backoffice/chat-tickets",
                   params={"workflow": "wallet_topup", "approval_required": False})
    assert r.json()["total"] == 1

    # Search by phone
    r = client.get("/api/backoffice/chat-tickets", params={"q": "0903000001"})
    assert r.json()["total"] == 1
    assert r.json()["tickets"][0]["subject_type"] == "merchant"

    # Search by user_id
    r = client.get("/api/backoffice/chat-tickets", params={"q": "U_TEST"})
    assert r.json()["total"] == 1

    # Search by merchant_id
    r = client.get("/api/backoffice/chat-tickets", params={"q": "MC_TEST"})
    assert r.json()["total"] == 1


# ─── Detail endpoint exposes staff identity, not to customer chat ──

def test_detail_endpoint_and_security(client):
    t = finalize_customer_chat_and_handoff(
        _wallet_session(), _wallet_ctx(), reason="ended",
    )
    r = client.get(f"/api/backoffice/chat-tickets/{t.ticket_id}")
    assert r.status_code == 200
    detail = r.json()
    # staff CAN see complainant identity
    assert detail["complainant"]["user_id"] == "U_TEST"
    assert detail["complainant"]["wallet_id"] == "W_TEST"
    # diagnosis is public-safe and present
    assert detail["selected_workflow"] == "wallet_topup"
    assert detail["public_safe_diagnosis"]["customer_safe_cause"]


def test_decision_endpoints_do_not_execute_money_movement(client):
    t = finalize_customer_chat_and_handoff(
        _merchant_session(), _merchant_ctx(), reason="ended",
        case_state={"rule_decision": {"action": "create_manual_payout_draft"},
                    "approval_required": True, "risk_level": "medium"},
    )
    r = client.post(f"/api/backoffice/chat-tickets/{t.ticket_id}/approve",
                    json={"actor": "staff1"})
    assert r.status_code == 200
    # Approve only changes ticket status — no payout field / execution result.
    body = r.json()
    assert body["backoffice_ticket_status"] == "approved"
    assert "payout_executed" not in body


# ─── Test: Extracted actual values persist (not labels) ────────

def test_extracted_info_actual_values_not_labels():
    """Actual extracted values (amount/time/bank) must be stored on the ticket."""
    ctx = _wallet_ctx()
    ctx.extracted_info = {
        "amount": 500000,
        "approximate_time_text": "9h sáng",
        "bank_name": "Vietcombank",
    }
    t = finalize_customer_chat_and_handoff(_wallet_session(), ctx, reason="ended")
    assert t.extracted_info["amount"] == 500000
    assert t.extracted_info["approximate_time_text"] == "9h sáng"
    assert t.extracted_info["bank_name"] == "Vietcombank"


# ─── Test: Placeholder labels are filtered out ─────────────────

def test_placeholder_labels_are_filtered():
    """_is_placeholder_label must reject generic labels, accept real values."""
    from fintech_agent.api.chat_handoff import _is_placeholder_label

    assert _is_placeholder_label("số tiền giao dịch") is True
    assert _is_placeholder_label("thời gian giao dịch") is True
    assert _is_placeholder_label("ngân hàng") is True
    assert _is_placeholder_label("mã giao dịch") is True
    assert _is_placeholder_label("unknown") is True
    assert _is_placeholder_label("") is True
    assert _is_placeholder_label(None) is True

    assert _is_placeholder_label("Vietcombank") is False
    assert _is_placeholder_label(500000) is False
    assert _is_placeholder_label("9h sáng") is False
    assert _is_placeholder_label("TXN_TOPUP_123") is False


def test_placeholder_labels_not_stored_in_ticket():
    """When LLM returns labels instead of values, extracted_info is filtered to empty."""
    from fintech_agent.api.chat_handoff import _to_detail

    ctx = _wallet_ctx()
    # Simulate malformed LLM output: keys have label strings instead of real values
    ctx.extracted_info = {
        "amount": "số tiền giao dịch",
        "approximate_time_text": "thời gian giao dịch",
        "bank_name": "ngân hàng",
    }
    t = finalize_customer_chat_and_handoff(_wallet_session(), ctx, reason="ended")
    # All placeholder values should be filtered out at storage time
    assert t.extracted_info.get("amount", None) is None
    assert t.extracted_info.get("approximate_time_text", None) is None
    assert t.extracted_info.get("bank_name", None) is None
    # CustomerProblemPublic should return empty strings, not labels
    detail = _to_detail(t)
    cp = detail.customer_problem_structured
    assert cp is not None
    assert cp.extracted_amount == ""
    assert cp.extracted_time == ""
    assert cp.extracted_bank_provider == ""


# ─── Test: Staff diagnosis includes customer claims ────────────

def test_staff_diagnosis_includes_extracted_claims(client):
    """Agent diagnosis must list customer-provided facts (amount/time/bank)."""
    ctx = _wallet_ctx()
    ctx.extracted_info = {
        "amount": 500000,
        "approximate_time_text": "9h sáng",
        "bank_name": "Vietcombank",
    }
    t = finalize_customer_chat_and_handoff(_wallet_session(), ctx, reason="ended",
                                           case_state={
                                               "rule_decision": {"action": "create_reconciliation_ticket_draft"},
                                               "approval_required": False, "risk_level": "low",
                                           })
    r = client.get(f"/api/backoffice/chat-tickets/{t.ticket_id}")
    assert r.status_code == 200
    body = r.json()

    # extracted_info in detail carries actual values
    assert body["extracted_info"]["amount"] == 500000
    assert body["extracted_info"]["bank_name"] == "Vietcombank"

    # customer_problem_structured uses real values, not labels
    cp = body["customer_problem_structured"]
    assert cp["extracted_amount"] == "500.000đ"
    assert cp["extracted_time"] == "9h sáng"
    assert cp["extracted_bank_provider"] == "Vietcombank"

    # agent_diagnosis confirmed_facts should mention customer claims
    diag = body["agent_diagnosis"]
    confirmed_blob = " ".join(str(f) for f in diag.get("confirmed_facts", []))
    assert "500.000đ" in confirmed_blob or "500000" in confirmed_blob
    assert "Vietcombank" in confirmed_blob
    assert "9h sáng" in confirmed_blob
