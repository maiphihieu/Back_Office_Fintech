"""Merchant settlement_delay customer-chat safety tests.

Regression target: the merchant settlement reply must NOT leak internal action
wording ("draft thanh toán thủ công" / "manual payout"), must NOT overpromise
payout, and must be generated from a public-safe diagnosis driven by the
settlement rule engine — not a fixed phrase.

All tests run at the logic layer (no network/LLM/Supabase), so they are
deterministic and prove the response comes from diagnosis + guardrail.
"""

import os
import re

import pytest

from fintech_agent.safety.evidence_mapper import build_public_safe_diagnosis
from fintech_agent.safety.output_guardrail import check_response_safety
from fintech_agent.llm.response_composer import compose_customer_response
from fintech_agent.llm.message_analyzer import MessageAnalysis


WF = "merchant_settlement_delay"

# Internal wording / overpromises that must NEVER reach a merchant customer.
_FORBIDDEN_IN_REPLY = [
    "manual payout",
    "thanh toán thủ công",
    "draft",
    "action_draft",
    "approval_packet",
    "batch_status",
    "payout_status",
    "rule_id",
    "đảm bảo bạn nhận được tiền",
    "chắc chắn nhận được",
    "chuyển tiền ngay",
    "force-success",
    "master wallet",
    "ledger",
    "reconciliation",
    "risk_score",
    "fraud_status",
]


def _diag(rule_action: str, evidence: dict | None = None, status: str = "need_more_info"):
    rule = {"action": rule_action} if rule_action else None
    return build_public_safe_diagnosis(WF, evidence or {}, rule, status)


def _reply(message_type: str, diagnosis: dict, status: str = "need_more_info") -> str:
    a = MessageAnalysis(
        message_type=message_type, workflow_hint=WF, customer_emotion="worried",
    )
    composed = compose_customer_response(
        "msg", a, {"selected_workflow": WF}, diagnosis, status,
    )
    return composed.public_message


def _assert_no_forbidden(text: str):
    low = text.lower()
    for term in _FORBIDDEN_IN_REPLY:
        assert term.lower() not in low, f"forbidden term leaked: {term!r} in: {text}"


# ─── Test 1: D+1 settlement delay new complaint ────────────────

class TestT1_SettlementDelayComplaint:
    def test_diagnosis_workflow_and_checks(self):
        d = _diag("create_manual_payout_draft")
        assert d["workflow"] == WF
        checks = " ".join(d["what_was_checked"]).lower()
        assert "settlement" in checks       # settlement cycle
        assert "giải ngân" in checks        # payout
        assert "ngân hàng" in checks        # bank account

    def test_issue_location_is_settlement_not_wallet(self):
        d = _diag("create_manual_payout_draft")
        loc = (d["likely_issue_location"] or "").lower()
        assert "giải ngân" in loc
        assert "số dư" not in loc and "ví" not in loc

    def test_reply_has_no_internal_action_or_overpromise(self):
        d = _diag("create_manual_payout_draft")
        reply = _reply("new_complaint", d)
        _assert_no_forbidden(reply)
        # references the settlement context
        assert "giải ngân" in reply.lower()

    def test_reply_passes_guardrail(self):
        d = _diag("create_manual_payout_draft")
        reply = _reply("new_complaint", d)
        g = check_response_safety(reply, None, workflow=WF, diagnosis=d)
        assert g.is_safe, g.violations

    def test_original_bad_reply_is_blocked(self):
        """The exact symptom reply must be rejected by the guardrail."""
        bad = (
            "Chúng tôi sẽ tạo một draft thanh toán thủ công để đảm bảo bạn "
            "nhận được số tiền này sớm nhất có thể."
        )
        d = _diag("create_manual_payout_draft")
        g = check_response_safety(bad, None, workflow=WF, diagnosis=d)
        assert not g.is_safe
        assert g.sanitized_text is not None


# ─── Test 2: "Tiền giải ngân đang bị kẹt ở đâu?" ───────────────

class TestT2_WhereIsTheMoney:
    def test_uses_settlement_diagnosis_not_generic_only(self):
        d = _diag("create_manual_payout_draft")
        reply = _reply("ask_where_money_is", d)
        low = reply.lower()
        # Must reference settlement/payout context, not be a bare "đang kiểm tra".
        assert "giải ngân" in low
        assert reply.strip() not in (
            "Yêu cầu của bạn đang được bộ phận phụ trách kiểm tra.",
        )
        _assert_no_forbidden(reply)

    def test_no_raw_internal_fields(self):
        d = _diag("manual_settlement_review")
        reply = _reply("ask_where_money_is", d)
        _assert_no_forbidden(reply)
        g = check_response_safety(reply, None, workflow=WF, diagnosis=d)
        assert g.is_safe, g.violations


# ─── Test 3: "Tôi có cần gửi lại số tài khoản ngân hàng không?" ─

class TestT3_BankAccountQuestion:
    def test_bank_issue_asks_safe_verification(self):
        d = _diag("request_bank_account_correction")
        reply = _reply("ask_what_to_do", d)
        low = reply.lower()
        assert "tài khoản ngân hàng" in low
        assert "kênh chính thức" in low          # verify via official channel
        # never asks for sensitive credentials
        _assert_no_forbidden(reply)
        g = check_response_safety(reply, None, workflow=WF, diagnosis=d)
        assert g.is_safe, g.violations

    def test_no_evidence_says_team_checking_bank_config(self):
        # No rule action → "checking" situation mentions bank account config.
        d = _diag("")
        reply = _reply("ask_what_to_do", d)
        low = reply.lower()
        assert "tài khoản ngân hàng" in low or "giải ngân" in low
        _assert_no_forbidden(reply)

    def test_does_not_request_credentials(self):
        d = _diag("request_bank_account_correction")
        reply = _reply("ask_what_to_do", d)
        # Guardrail must not flag a credential request in this reply.
        g = check_response_safety(reply, None, workflow=WF, diagnosis=d)
        assert g.is_safe, g.violations


# ─── Test 4: Code scan — no hard-coded merchant replies ────────

class TestT4_NoHardCode:
    # Customer-facing response-composition logic (NOT the guardrail, which
    # legitimately lists forbidden terms, and NOT staff message templates).
    RESPONSE_FILES = [
        "api/customer_chat.py",
        "api/generic_resolver.py",
        "safety/evidence_mapper.py",
        "llm/response_composer.py",
    ]
    # Files scanned for IDs only (broader set).
    ALL_PIPELINE = RESPONSE_FILES + ["safety/output_guardrail.py"]

    def _read(self, rel: str) -> str:
        base = os.path.join(os.path.dirname(__file__), "..", "src", "fintech_agent")
        with open(os.path.abspath(os.path.join(base, rel)), encoding="utf-8") as f:
            return f.read()

    def _scan(self, files: list[str], pattern: str) -> list[str]:
        hits: list[str] = []
        for rel in files:
            for i, line in enumerate(self._read(rel).split("\n"), 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                if re.search(pattern, s, re.IGNORECASE):
                    hits.append(f"{rel}:{i}: {s[:90]}")
        return hits

    def test_no_hardcoded_exact_complaint(self):
        assert self._scan(self.ALL_PIPELINE, r"chưa nhận được tiền giải ngân") == []

    def test_no_hardcoded_batch_fail_id(self):
        assert self._scan(self.ALL_PIPELINE, r"MRC_001_BATCH_FAIL") == []

    def test_no_manual_payout_promise_in_response_logic(self):
        for pat in (r"manual\s+payout", r"thanh\s+toán\s+thủ\s+công",
                    r"đảm\s+bảo.{0,20}nhận\s+được.{0,10}tiền"):
            assert self._scan(self.RESPONSE_FILES, pat) == [], f"found {pat}"

    def test_no_message_equality_branching(self):
        hits = self._scan(self.RESPONSE_FILES, r"customer_message\s*==")
        hits += self._scan(self.RESPONSE_FILES, r"message\s*==\s*[\"']")
        assert hits == []
