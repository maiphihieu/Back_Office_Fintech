"""Tests for the back-office chat-ticket investigation.

Proves the investigation is fully DATA-DRIVEN (nothing hard-coded per case):
  - the resolver builds ExtractedInfo from ticket data + complainant identity,
  - evidence lookup runs for the resolved entity,
  - the diagnosis is specific when evidence exists,
  - missing evidence is named exactly when it is absent,
  - the staff action contract explains what approve does / does not do,
  - changing the inputs changes the outputs (no constant answers).

Evidence lookup is injected with stubs, so these are deterministic and offline
(no Supabase / no MCP subprocess).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import fintech_agent.api.ticket_investigation as inv_mod
from fintech_agent.api.chat_handoff import (
    get_ticket_store,
    reset_ticket_store,
)
from fintech_agent.api.ticket_investigation import (
    build_extracted_info,
    investigate_customer_chat_ticket,
)
from fintech_agent.main import create_app
from fintech_agent.schemas.chat_handoff import ChatHandoffTicket, ComplainantInfo
from fintech_agent.schemas.evidence import (
    AccountStatus,
    EvidenceBundle,
    FraudCase,
    ReconciliationStatus,
    Transaction,
)


# ─── Fixtures / builders (no hard-coded production data) ─────────

@pytest.fixture(autouse=True)
def _clean_store():
    reset_ticket_store()
    yield
    reset_ticket_store()


def _wallet_ticket(ticket_id="CHT_W", txn_id="TXN_ABC", amount=500000) -> ChatHandoffTicket:
    t = ChatHandoffTicket(ticket_id=ticket_id, selected_workflow="wallet_topup")
    t.complainant = ComplainantInfo(subject_type="wallet_user", user_id="U_X", wallet_id="W_X")
    t.extracted_info = {"transaction_id": txn_id, "amount": amount, "bank_name": "Vietcombank"}
    return t


def _wallet_evidence(txn_id="TXN_ABC", amount=500000, *, bank="success", received=True):
    def _fetch(state):
        ev = EvidenceBundle(
            transaction=Transaction(
                transaction_id=txn_id, user_id="U_X", service_type="wallet_topup",
                amount=amount, status="pending",
            ),
            reconciliation_status=ReconciliationStatus(
                transaction_id=txn_id, bank_status=bank,
                money_received_in_master_wallet=received,
            ),
        )
        return {
            "evidence_bundle": ev,
            "tool_results": {"transaction": "ok", "reconciliation": "ok",
                             "identity_source": "session"},
            "selected_workflow": "wallet_topup",
        }
    return _fetch


# ─── Resolver builds ExtractedInfo from ticket data ──────────────

def test_resolver_builds_extracted_info_for_transaction_workflow():
    t = _wallet_ticket(txn_id="TXN_RESOLVE")
    info, entity_id, entity_type = build_extracted_info(t)
    assert entity_type == "transaction"
    assert entity_id == "TXN_RESOLVE"
    assert info.transaction_id == "TXN_RESOLVE"
    assert info.user_id == "U_X"  # identity from trusted complainant


def test_resolver_uses_complainant_identity_for_fraud_and_merchant():
    # fraud → user_id from complainant
    tf = ChatHandoffTicket(ticket_id="CHT_F", selected_workflow="fraud_account_lock")
    tf.complainant = ComplainantInfo(subject_type="wallet_user", user_id="U_FRAUD", phone="0900")
    info_f, id_f, type_f = build_extracted_info(tf)
    assert type_f == "account" and id_f == "U_FRAUD"
    assert info_f.user_id == "U_FRAUD"
    assert str(info_f.service_type) == "account_security"

    # merchant → merchant_id from complainant
    tm = ChatHandoffTicket(ticket_id="CHT_M", selected_workflow="merchant_settlement_delay")
    tm.complainant = ComplainantInfo(subject_type="merchant", merchant_id="MC_9", tax_code="TX9")
    tm.extracted_info = {"payout_id": "PO_1"}
    info_m, id_m, type_m = build_extracted_info(tm)
    assert type_m == "merchant" and id_m == "MC_9"
    assert info_m.merchant_id == "MC_9"
    assert info_m.payout_id == "PO_1"
    assert str(info_m.service_type) == "merchant_settlement"


def test_resolver_ignores_placeholder_label_as_transaction_id():
    t = _wallet_ticket()
    t.extracted_info = {"transaction_id": "mã giao dịch"}  # placeholder label, not a real id
    _, entity_id, _ = build_extracted_info(t)
    assert entity_id == ""  # placeholder must not become a real entity id


# ─── Evidence lookup is invoked for the resolved entity ──────────

def test_evidence_lookup_invoked_with_resolved_entity():
    t = _wallet_ticket(txn_id="TXN_SEEN")
    seen = {}

    def _capture(state):
        seen["extracted"] = state.get("extracted_info")
        seen["workflow"] = state.get("selected_workflow")
        return {"evidence_bundle": EvidenceBundle(), "tool_results": {},
                "selected_workflow": "wallet_topup"}

    investigate_customer_chat_ticket(t, fetch_evidence_fn=_capture)
    assert seen["extracted"].transaction_id == "TXN_SEEN"
    assert seen["workflow"] == "wallet_topup"


def test_no_evidence_fetch_when_entity_unresolved():
    t = _wallet_ticket()
    t.extracted_info = {"amount": 500000}  # no transaction_id

    def _must_not_run(state):
        raise AssertionError("evidence fetch must not run without a resolved entity")

    inv = investigate_customer_chat_ticket(
        t, fetch_evidence_fn=_must_not_run,
        search_transactions_fn=lambda u, f, w: [],  # search finds nothing
    )
    assert inv.resolved is False
    assert inv.resolver_status == "no_match"
    assert inv.missing_evidence  # states exactly what is missing
    assert any("giao dịch" in m.lower() for m in inv.missing_evidence)


def test_resolver_searches_by_amount_bank_when_no_transaction_id():
    """When the chat captured no transaction_id, the resolver searches and
    pins down the transaction from amount/bank/time → a specific diagnosis."""
    from types import SimpleNamespace

    t = _wallet_ticket(txn_id=None)  # type: ignore[arg-type]
    t.extracted_info = {"amount": 500000, "bank_name": "Vietcombank",
                        "approximate_time_text": "9h sáng"}
    seen = {}

    def _search(user_id, fields, workflow):
        seen["user_id"] = user_id
        seen["amount"] = fields.amount
        seen["bank"] = fields.bank_name
        return [SimpleNamespace(transaction_id="TXN_RESOLVED", user_id="U_X")]

    inv = investigate_customer_chat_ticket(
        t,
        search_transactions_fn=_search,
        fetch_evidence_fn=_wallet_evidence("TXN_RESOLVED", 500000, bank="success", received=True),
    )
    assert seen == {"user_id": "U_X", "amount": 500000, "bank": "Vietcombank"}
    assert inv.resolver_status == "resolved_by_search"
    assert inv.resolved_entity_id == "TXN_RESOLVED"
    assert inv.resolved is True
    assert inv.rule_action == "create_force_success_draft"


def test_multiple_candidates_reported_as_missing_specificity():
    from types import SimpleNamespace

    t = _wallet_ticket(txn_id=None)  # type: ignore[arg-type]
    t.extracted_info = {"amount": 500000, "bank_name": "Vietcombank"}

    def _search(user_id, fields, workflow):
        return [SimpleNamespace(transaction_id="TXN_1"), SimpleNamespace(transaction_id="TXN_2")]

    inv = investigate_customer_chat_ticket(
        t, search_transactions_fn=_search,
        fetch_evidence_fn=lambda s: (_ for _ in ()).throw(AssertionError("no fetch")),
    )
    assert inv.resolver_status == "multiple_candidates"
    assert inv.resolved is False
    assert any("chính xác" in m.lower() or "tham chiếu" in m.lower() for m in inv.missing_evidence)


def test_dev_log_redacts_secrets(caplog):
    import logging
    t = _wallet_ticket(txn_id=None)  # type: ignore[arg-type]
    t.extracted_info = {"amount": 500000, "otp": "999111", "password": "x"}
    with caplog.at_level(logging.INFO):
        investigate_customer_chat_ticket(t, search_transactions_fn=lambda u, f, w: [])
    line = next(r.message for r in caplog.records
               if "customer_ticket_investigation" in r.message)
    assert "999111" not in line and "***redacted***" in line
    assert '"resolver_called": true' in line
    assert '"resolver_status": "no_match"' in line


# ─── Diagnosis is specific when evidence exists ──────────────────

def test_diagnosis_specific_when_evidence_present():
    t = _wallet_ticket(txn_id="TXN_SPEC", amount=750000)
    inv = investigate_customer_chat_ticket(
        t, fetch_evidence_fn=_wallet_evidence("TXN_SPEC", 750000, bank="success", received=True),
    )
    assert inv.resolved is True
    assert inv.rule_action == "create_force_success_draft"
    assert inv.rule_diagnosis_code == "bank_success_money_received_wallet_pending"
    assert inv.approval_required is True
    assert inv.confidence == "high"
    # likely issue is specific, not a vague placeholder
    assert "ví" in inv.likely_issue.lower()
    assert "đang được" not in inv.likely_issue  # not the vague "đang được xác định"
    # confirmed facts carry REAL values from the evidence
    joined = " ".join(inv.confirmed_facts)
    assert "TXN_SPEC" in joined
    assert "750.000đ" in joined


def test_missing_evidence_named_when_absent():
    """No reconciliation data → diagnosis says exactly what is missing."""
    t = _wallet_ticket(txn_id="TXN_PARTIAL")

    def _only_txn(state):
        ev = EvidenceBundle(transaction=Transaction(
            transaction_id="TXN_PARTIAL", user_id="U_X", service_type="wallet_topup",
            amount=500000, status="pending"))
        return {"evidence_bundle": ev, "tool_results": {"transaction": "ok"},
                "selected_workflow": "wallet_topup"}

    inv = investigate_customer_chat_ticket(t, fetch_evidence_fn=_only_txn)
    assert "Đối soát ngân hàng" in inv.missing_evidence
    assert inv.rule_diagnosis_code == "reconciliation_unavailable"
    # evidence_summary marks the present source checked and the missing one missing
    by_label = {row["label"]: row["status"] for row in inv.evidence_summary}
    assert by_label["Giao dịch nạp tiền"] == "checked"
    assert by_label["Đối soát ngân hàng"] == "missing"


# ─── No hard-coded answers: outputs follow inputs ────────────────

def test_outputs_change_with_inputs_no_constants():
    # Same workflow, different evidence → different diagnosis code/action.
    t1 = _wallet_ticket(ticket_id="CHT_A", txn_id="TXN_A", amount=100000)
    inv1 = investigate_customer_chat_ticket(
        t1, fetch_evidence_fn=_wallet_evidence("TXN_A", 100000, bank="success", received=True))

    t2 = _wallet_ticket(ticket_id="CHT_B", txn_id="TXN_B", amount=200000)
    inv2 = investigate_customer_chat_ticket(
        t2, fetch_evidence_fn=_wallet_evidence("TXN_B", 200000, bank="failed", received=False))

    assert inv1.rule_diagnosis_code != inv2.rule_diagnosis_code
    assert inv1.rule_action != inv2.rule_action
    assert "TXN_A" in " ".join(inv1.confirmed_facts)
    assert "TXN_B" in " ".join(inv2.confirmed_facts)
    assert "100.000đ" in " ".join(inv1.confirmed_facts)
    assert "200.000đ" in " ".join(inv2.confirmed_facts)


def test_fraud_evidence_produces_specific_diagnosis():
    t = ChatHandoffTicket(ticket_id="CHT_FR", selected_workflow="fraud_account_lock")
    t.complainant = ComplainantInfo(subject_type="wallet_user", user_id="U_FR")

    def _fraud_fetch(state):
        ev = EvidenceBundle(
            account_status=AccountStatus(user_id="U_FR", account_status="locked",
                                         lock_reason="fraud_review"),
            fraud_case=FraudCase(fraud_case_id="FC_1", user_id="U_FR",
                                 fraud_status="reviewing",
                                 recommended_decision="likely_false_positive"),
        )
        return {"evidence_bundle": ev,
                "tool_results": {"account_status": "ok", "fraud_case": "ok",
                                 "identity_source": "session"},
                "selected_workflow": "fraud_account_lock"}

    inv = investigate_customer_chat_ticket(t, fetch_evidence_fn=_fraud_fetch)
    assert inv.resolved is True
    assert inv.risk_level == "high"  # fraud is always high risk
    assert any("Tài khoản" in f for f in inv.confirmed_facts)


# ─── Detail endpoint runs the investigation (wired) ──────────────

def test_detail_endpoint_runs_investigation_and_shows_action(monkeypatch):
    # Inject a deterministic evidence fetcher into the real node slot.
    monkeypatch.setattr(
        inv_mod, "_default_fetch_evidence",
        _wallet_evidence("TXN_ENDPOINT", 500000, bank="success", received=True),
    )

    t = _wallet_ticket(ticket_id="CHT_EP", txn_id="TXN_ENDPOINT")
    get_ticket_store().upsert("chat:CHT_EP", t)

    client = TestClient(create_app())
    r = client.get(f"/api/backoffice/chat-tickets/{t.ticket_id}")
    assert r.status_code == 200
    body = r.json()

    # Specific, data-driven diagnosis (not vague)
    diag = body["agent_diagnosis"]
    assert diag["confidence"] == "high"
    assert "ví" in diag["likely_bottleneck"].lower()
    assert any("TXN_ENDPOINT" in f for f in diag["confirmed_facts"])

    # Evidence checklist shows real queried sources, all checked
    checklist = body["evidence_checklist"]
    labels = {c["label"]: c["status"] for c in checklist}
    assert labels.get("Giao dịch nạp tiền") == "checked"
    assert labels.get("Đối soát ngân hàng") == "checked"

    # Staff action contract explains approve behaviour and stays safe
    action = body["staff_action"]
    assert action["action_type"] == "create_force_success_draft"
    assert action["approve_effect"]            # what approve DOES
    assert action["approve_does_not_do"]       # what approve does NOT do
    # money-movement denials always present
    denials = " ".join(action["approve_does_not_do"]).lower()
    assert "ledger" in denials or "số dư" in denials

    # Section F: money/issue location is explicit AND distinct from the bottleneck
    assert body["money_or_issue_location"]
    assert body["money_or_issue_location"] == diag["money_or_issue_location"]
    assert diag["likely_bottleneck"] != diag["money_or_issue_location"]
    assert "ví" in diag["likely_bottleneck"].lower()  # action-derived stuck step
    # resolved_entity is surfaced explicitly
    assert body["resolved_entity"]["type"] == "transaction"
    assert body["resolved_entity"]["id"] == "TXN_ENDPOINT"


def test_detail_endpoint_shows_missing_evidence_when_absent(monkeypatch):
    def _only_txn(state):
        ev = EvidenceBundle(transaction=Transaction(
            transaction_id="TXN_M", user_id="U_X", service_type="wallet_topup",
            amount=500000, status="pending"))
        return {"evidence_bundle": ev, "tool_results": {"transaction": "ok"},
                "selected_workflow": "wallet_topup"}

    monkeypatch.setattr(inv_mod, "_default_fetch_evidence", _only_txn)

    t = _wallet_ticket(ticket_id="CHT_MISS", txn_id="TXN_M")
    get_ticket_store().upsert("chat:CHT_MISS", t)

    client = TestClient(create_app())
    body = client.get(f"/api/backoffice/chat-tickets/{t.ticket_id}").json()

    checklist = body["evidence_checklist"]
    labels = {c["label"]: c["status"] for c in checklist}
    assert labels.get("Đối soát ngân hàng") == "missing"
    # Missing evidence surfaces in the staff action panel too
    missing_text = " ".join(body["staff_action"]["missing_preconditions"])
    assert "Đối soát ngân hàng" in missing_text
    # ...and as an explicit top-level field + on the diagnosis
    assert "Đối soát ngân hàng" in body["missing_evidence"]
    assert "Đối soát ngân hàng" in body["agent_diagnosis"]["missing_evidence"]


def test_detail_endpoint_unresolved_routes_to_manual_review(monkeypatch):
    """Evidence insufficient → primary action is manual review, not approve."""
    # Resolver search finds no matching transaction → unresolved.
    monkeypatch.setattr(inv_mod, "_default_search_transactions",
                        lambda user_id, fields, wf: [])

    t = _wallet_ticket(ticket_id="CHT_UNRES", txn_id=None)  # type: ignore[arg-type]
    t.extracted_info = {"amount": 500000, "bank_name": "Vietcombank"}
    get_ticket_store().upsert("chat:CHT_UNRES", t)

    client = TestClient(create_app())
    body = client.get(f"/api/backoffice/chat-tickets/{t.ticket_id}").json()

    action = body["staff_action"]
    assert action["action_type"] == "manual_review"
    assert action["approval_required"] is False
    # The approve label must NOT promise "Phê duyệt xử lý" for unresolved evidence
    assert not action["approve_button_label"].startswith("Phê duyệt tạo draft xử lý")
    # Missing evidence is stated, not a vague placeholder
    missing = " ".join(action["missing_preconditions"])
    assert "giao dịch" in missing.lower()


def test_investigation_only_recommends_draft_actions():
    """Safety: investigation never yields an execute/auto money action."""
    t = _wallet_ticket(txn_id="TXN_SAFE")
    inv = investigate_customer_chat_ticket(
        t, fetch_evidence_fn=_wallet_evidence("TXN_SAFE", bank="success", received=True))
    # The only money-related action is a *draft* requiring human approval.
    assert inv.rule_action.endswith("_draft") or inv.rule_action in (
        "manual_review", "draft_customer_response", "no_action", "wait_sla",
    )
    assert "execute" not in inv.rule_action
