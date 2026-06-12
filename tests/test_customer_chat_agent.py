"""Context-aware customer chat agent — behavior tests.

These are deterministic and offline: message classification is exercised via
the analyzer's generic fallback (OPENAI_API_KEY cleared), and the rest are pure
units. No hard-coded production data, phrases, or LLM calls.

Covers the required scenarios:
  - customer doesn't know transaction_id      → follow_up (no new case)
  - customer provides amount/time instead      → provide_missing_info
  - customer asks bot to check their account   → follow_up
  - customer complains "why not resolved"      → follow_up
  - customer changes the amount                → contradiction detected
  - off-topic message                          → out_of_scope, no case, no ticket
  - no duplicate case for a follow-up          → follow_up classification
  - no fake confirmation without evidence      → claims stay unverified
  - backend field names never leak to customer → sanitizer
"""

from __future__ import annotations

import pytest

from fintech_agent.api.customer_claims import (
    CustomerClaims,
    VerifiedEvidence,
    detect_contradictions,
    get_unverified_claims,
)
from fintech_agent.safety.output_guardrail import sanitize_customer_text


@pytest.fixture
def offline_analyzer(monkeypatch):
    """Force the analyzer's deterministic fallback (no LLM)."""
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from fintech_agent.llm.message_analyzer import analyze_customer_message
    return analyze_customer_message


_ACTIVE = {"selected_workflow": "wallet_topup", "has_active_case": True}
_SESSION = {"subject_type": "wallet_user", "is_authenticated": True}


# ─── Message classification (active-case awareness) ──────────────

def test_unknown_transaction_id_is_followup_not_new_case(offline_analyzer):
    a = offline_analyzer("Tôi không nhớ mã giao dịch", _ACTIVE, _SESSION)
    assert a.message_type == "follow_up"          # not new_complaint → no new case
    assert a.workflow_hint == "wallet_topup"


def test_provide_amount_time_bank_is_missing_info(offline_analyzer):
    a = offline_analyzer("Tôi nạp 500000 lúc 9h sáng qua Vietcombank", _ACTIVE, _SESSION)
    assert a.message_type == "provide_missing_info"
    assert a.extracted.amount == 500000
    assert a.extracted.bank_name  # bank captured as an alternative lookup key


def test_ask_check_account_is_followup(offline_analyzer):
    a = offline_analyzer("Bạn kiểm tra tài khoản của tôi đi", _ACTIVE, _SESSION)
    assert a.message_type == "follow_up"


def test_complaint_why_not_resolved_is_followup(offline_analyzer):
    a = offline_analyzer("Sao mãi chưa giải quyết cho tôi vậy", _ACTIVE, _SESSION)
    assert a.message_type == "follow_up"


def test_followup_messages_never_classify_as_new_complaint(offline_analyzer):
    # A handful of typical follow-up phrasings, all on an active case.
    for msg in [
        "tôi không biết lấy mã giao dịch ở đâu",
        "ai xử lý vấn đề này",
        "bao giờ xong",
    ]:
        a = offline_analyzer(msg, _ACTIVE, _SESSION)
        assert a.message_type != "new_complaint", msg


# ─── Off-topic / greeting → not a case ───────────────────────────

def test_offtopic_and_greeting_detected(offline_analyzer):
    assert offline_analyzer("Xin chào shop", {}, _SESSION).message_type == "greeting"
    for msg in ["1 + 1 bằng mấy", "kể chuyện cười đi", "đặt vé máy bay giúp tôi"]:
        a = offline_analyzer(msg, _ACTIVE, _SESSION)
        assert a.message_type == "out_of_scope", msg
        assert a.workflow_hint == "unknown"      # never forced into a workflow


def test_offtopic_does_not_create_ticket():
    from fintech_agent.api.customer_chat import _should_create_ticket
    from fintech_agent.api.generic_resolver import ResolutionResult
    from fintech_agent.llm.message_analyzer import ActiveCaseContext, MessageAnalysis

    analysis = MessageAnalysis(message_type="out_of_scope")
    res = ResolutionResult(resolution_status="need_more_info")
    assert _should_create_ticket(analysis, res, None, ActiveCaseContext()) is False


# ─── Ticket handoff gating (#6) ──────────────────────────────────

def test_resolved_in_chat_does_not_create_ticket():
    from fintech_agent.api.customer_chat import _should_create_ticket
    from fintech_agent.api.generic_resolver import ResolutionResult
    from fintech_agent.llm.message_analyzer import ActiveCaseContext, MessageAnalysis

    analysis = MessageAnalysis(message_type="follow_up")
    res = ResolutionResult(resolution_status="resolved")
    ctx = ActiveCaseContext(case_id="CASE_X", selected_workflow="wallet_topup")
    assert _should_create_ticket(analysis, res, None, ctx) is False


def test_unresolved_or_rule_decision_creates_ticket():
    from fintech_agent.api.customer_chat import _should_create_ticket
    from fintech_agent.api.generic_resolver import ResolutionResult
    from fintech_agent.llm.message_analyzer import ActiveCaseContext, MessageAnalysis

    analysis = MessageAnalysis(message_type="follow_up")
    ctx = ActiveCaseContext(case_id="CASE_X", selected_workflow="wallet_topup")
    # unresolved financial issue
    assert _should_create_ticket(
        analysis, ResolutionResult(resolution_status="need_more_info"), None, ctx,
    ) is True
    # rule engine produced a decision
    assert _should_create_ticket(
        analysis, ResolutionResult(resolution_status="resolved"),
        {"rule_decision": {"action": "create_force_success_draft"}}, ctx,
    ) is True


# ─── Claim vs verified evidence (#2) ─────────────────────────────

def test_customer_claim_is_not_verified_without_evidence():
    claims = CustomerClaims()
    claims.merge_claim("amount", 500000)
    claims.merge_claim("bank_name", "Vietcombank")
    # No evidence verified yet → both remain unverified.
    unverified = get_unverified_claims(claims, VerifiedEvidence())
    fields = {u["field"] for u in unverified}
    assert "amount" in fields and "bank_name" in fields


def test_changed_amount_supersedes_and_contradicts_evidence():
    claims = CustomerClaims()
    claims.merge_claim("amount", 500000)
    # Customer corrects the amount.
    claims.merge_claim("amount", 300000, is_correction=True)
    assert claims.latest["amount"] == 300000
    assert any(r.superseded for r in claims.history)

    # System verified the real amount is 500000 → contradiction surfaced.
    evidence = VerifiedEvidence(
        resolved_entity_id="TXN_X", verified_amount=500000,
        evidence_source="transaction_table",
    )
    contradictions = detect_contradictions(claims, evidence)
    assert len(contradictions) == 1
    c = contradictions[0]
    assert c.field == "amount"
    assert c.customer_claim == 300000 and c.verified_value == 500000


def test_no_contradiction_when_evidence_missing():
    """Without verified evidence we never 'confirm' — and never contradict."""
    claims = CustomerClaims()
    claims.merge_claim("amount", 500000)
    assert detect_contradictions(claims, VerifiedEvidence()) == []


# ─── Customer-safe wording: no backend field names (#4) ──────────

def test_sanitizer_strips_field_name_parenthetical():
    out = sanitize_customer_text(
        "Bạn vui lòng cung cấp mã giao dịch (transaction_id) để kiểm tra."
    )
    assert "transaction_id" not in out
    assert "mã giao dịch" in out
    assert "()" not in out


def test_sanitizer_replaces_standalone_tokens_with_labels():
    out = sanitize_customer_text("Vui lòng gửi transaction_id và bank_reference.")
    assert "transaction_id" not in out and "bank_reference" not in out
    assert "mã giao dịch" in out
    assert "mã tham chiếu ngân hàng" in out


def test_sanitizer_handles_multiple_fields_and_is_idempotent():
    raw = "Kiểm tra amount, transaction_time, order_id của bạn."
    once = sanitize_customer_text(raw)
    assert "amount" not in once and "transaction_time" not in once and "order_id" not in once
    # Running again must not change a clean string.
    assert sanitize_customer_text(once) == once


def test_sanitizer_leaves_clean_vietnamese_untouched():
    clean = "Chúng tôi đang kiểm tra giao dịch nạp tiền của bạn."
    assert sanitize_customer_text(clean) == clean


# ─── Workflow switch: a different service mid-chat starts a fresh case ──

def _ctx(workflow=""):
    from fintech_agent.llm.message_analyzer import ActiveCaseContext
    return ActiveCaseContext(case_id="CASE_W", selected_workflow=workflow)


def _ana(message_type, workflow_hint):
    from fintech_agent.llm.message_analyzer import MessageAnalysis
    return MessageAnalysis(message_type=message_type, workflow_hint=workflow_hint)


def test_workflow_switch_detected_for_different_known_workflow():
    from fintech_agent.api.customer_chat import _is_workflow_switch
    # active wallet_topup → customer now raises a train_ticket problem
    assert _is_workflow_switch(_ctx("wallet_topup"), _ana("workflow_switch", "train_ticket")) is True
    assert _is_workflow_switch(_ctx("wallet_topup"), _ana("new_complaint", "fraud_account_lock")) is True


def test_workflow_switch_false_for_same_workflow_or_followup():
    from fintech_agent.api.customer_chat import _is_workflow_switch
    # same workflow → not a switch
    assert _is_workflow_switch(_ctx("wallet_topup"), _ana("new_complaint", "wallet_topup")) is False
    # a follow-up/status message about the active case is NOT a switch even if the
    # hint momentarily differs
    assert _is_workflow_switch(_ctx("wallet_topup"), _ana("follow_up", "train_ticket")) is False
    assert _is_workflow_switch(_ctx("wallet_topup"), _ana("ask_status", "train_ticket")) is False
    # no active workflow → nothing to switch from
    assert _is_workflow_switch(_ctx(""), _ana("new_complaint", "train_ticket")) is False
    # unknown/faq hint → not a switch
    assert _is_workflow_switch(_ctx("wallet_topup"), _ana("workflow_switch", "unknown")) is False


def test_fresh_case_drops_old_workflow_state_keeps_conversation():
    from fintech_agent.api.customer_chat import _start_fresh_case_for_switch
    from fintech_agent.api.customer_claims import CustomerClaims
    from fintech_agent.llm.message_analyzer import ActiveCaseContext

    old = ActiveCaseContext(
        case_id="CASE_WALLET", selected_workflow="wallet_topup",
        subject_type="wallet_user", customer_problem="nạp tiền chưa vào ví",
        missing_fields=["amount"], extracted_info={"amount": 500000},
        resolved_entity_id="TXN_OLD",
        last_diagnosis={"customer_safe_cause": "ví chưa cập nhật"},
        transcript=[{"role": "customer", "text": "x", "timestamp": "t"}],
    )
    old._customer_claims = CustomerClaims()
    old._customer_claims.merge_claim("amount", 500000)

    fresh = _start_fresh_case_for_switch(old, "vé tàu chưa nhận được")

    # New logical case — no carryover from the wallet workflow.
    assert fresh.case_id == ""
    assert fresh.selected_workflow == ""
    assert fresh.extracted_info == {}
    assert fresh.resolved_entity_id is None
    assert fresh.last_diagnosis == {}
    assert fresh.missing_fields == []
    assert fresh._customer_claims.latest == {}      # fresh claims
    assert fresh._contradictions == []
    # Conversation + identity continue.
    assert fresh.subject_type == "wallet_user"
    assert fresh.transcript == old.transcript
    assert "vé tàu" in fresh.customer_problem
