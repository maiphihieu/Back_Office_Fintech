"""Multi-issue case-transition logic.

A single chat may contain several distinct complaints. decide_case_transition()
must never let a finished / no-issue case block the next complaint, and must
never reuse the previous workflow's diagnosis for a different workflow.

Pure unit tests + one integration test reproducing the live bug (account-lock
no-match followed by a top-up complaint).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import fintech_agent.api.customer_chat as cc
from fintech_agent.api.case_transition import decide_case_transition
from fintech_agent.main import create_app


def _router(message_type, workflow_hint="unknown"):
    return {"message_type": message_type, "workflow_hint": workflow_hint}


def _case(workflow="", status="", case_id=""):
    return {"workflow": workflow, "status": status, "case_id": case_id}


# ─── decide_case_transition (pure) ───────────────────────────────

def test_no_active_case_is_new_case():
    t = decide_case_transition(_router("new_complaint", "wallet_topup"), _case())
    assert t.transition == "new_case"
    assert t.reuse_old_diagnosis is False
    assert t.target_workflow == "wallet_topup"


def test_new_complaint_different_workflow_switches():
    t = decide_case_transition(
        _router("new_complaint", "wallet_topup"),
        _case(workflow="fraud_account_lock", status="no_match"),
    )
    assert t.transition == "workflow_switch"
    assert t.reuse_old_diagnosis is False
    assert t.target_workflow == "wallet_topup"


def test_new_complaint_after_finished_case_is_new_case():
    # Same workflow but the active case already found no issue → fresh case.
    t = decide_case_transition(
        _router("new_complaint", "fraud_account_lock"),
        _case(workflow="fraud_account_lock", status="no_match"),
    )
    assert t.transition == "new_case"
    assert t.reuse_old_diagnosis is False


def test_new_complaint_unknown_workflow_still_new_case_not_followup():
    # Bug case: lock case finished, vague top-up complaint, analyzer unsure.
    t = decide_case_transition(
        _router("new_complaint", "unknown"),
        _case(workflow="fraud_account_lock", status="no_match", case_id="C1"),
    )
    assert t.transition in ("new_case", "workflow_switch")
    assert t.reuse_old_diagnosis is False


def test_followup_stays_same_case_and_reuses_diagnosis():
    t = decide_case_transition(
        _router("follow_up", "wallet_topup"),
        _case(workflow="wallet_topup", status="resolved", case_id="C1"),
    )
    assert t.transition == "same_case"
    assert t.reuse_old_diagnosis is True
    assert t.target_workflow == "wallet_topup"


def test_offtopic_is_not_a_case():
    t = decide_case_transition(_router("out_of_scope", "unknown"),
                               _case(workflow="wallet_topup", case_id="C1"))
    assert t.transition == "off_topic"
    assert t.reuse_old_diagnosis is False


def test_workflow_switch_message_type_is_switch():
    t = decide_case_transition(
        _router("workflow_switch", "train_ticket"),
        _case(workflow="wallet_topup", status="resolved", case_id="C1"),
    )
    assert t.transition == "workflow_switch"
    assert t.target_workflow == "train_ticket"
    assert t.reuse_old_diagnosis is False


def test_demo_profile_name_never_in_decision():
    # The decision only uses router intent + active case — never identity/profile.
    t = decide_case_transition(_router("new_complaint", "wallet_topup"), _case())
    assert "demo" not in (t.target_workflow + t.reason).lower()


# ─── Integration: lock no-match → top-up complaint ───────────────

@pytest.fixture
def client():
    cc._session_context.clear()
    cc._session_last_active.clear()
    return TestClient(create_app())


def test_lock_nomatch_then_topup_verifies_topup_not_insist(client):
    """Live bug: account-lock complaint (no lock found) then a top-up complaint.
    The top-up must be verified from the account's own data, NOT answered as
    insistence on the lock no-match, and never with an 'id not found' reply."""
    S = "demo_customer_topup"   # account NOT locked; HAS a pending top-up
    r1 = client.post("/api/customer-chat", json={
        "message": "tài khoản tôi bị khóa", "session_id": S,
    }).json()
    assert "chưa tìm thấy bản ghi khóa" in r1["public_response"].lower()

    r2 = client.post("/api/customer-chat", json={
        "message": "tôi nạp tiền chưa vào ví", "session_id": S,
    }).json()
    resp = r2["public_response"].lower()
    # Must NOT be the lock-insist / id-not-found template.
    assert "chắc chắn đã thực hiện giao dịch" not in resp
    assert "chưa tìm thấy mã này" not in resp
    # Must NOT carry account-lock wording.
    assert "khóa" not in resp and "bảo mật" not in resp
    # Must be a wallet-topup answer grounded in the account's data.
    assert "ví" in resp or "nạp" in resp


def test_topup_then_train_no_stale_wallet_wording(client):
    """A second, different complaint must not reuse the first workflow wording."""
    S = "demo_customer_topup"
    client.post("/api/customer-chat", json={
        "message": "tôi nạp tiền chưa vào ví", "session_id": S})
    r2 = client.post("/api/customer-chat", json={
        "message": "à mà vé tàu tôi mua sao chưa nhận được vé", "session_id": S}).json()
    resp = r2["public_response"].lower()
    # No stale wallet-topup wording in a train answer.
    assert "số dư ví" not in resp and "nạp ví" not in resp
