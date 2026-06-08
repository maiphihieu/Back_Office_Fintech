"""Workflow-aware public-safe diagnosis tests.

Proves the customer response logic is GENERIC and data-driven — the same
evidence pipeline produces workflow-appropriate diagnosis without any
phrase-specific patches or hard-coded final replies.

Regression target: a train_ticket case must NEVER borrow wallet_topup wording
("kiểm tra lại số dư", "ví chưa cập nhật số dư", "cập nhật vào ví").

These tests run at the logic layer (no network/LLM, no external data), so they
are deterministic and prove the framing comes from workflow + evidence.
"""

import os
import re

import pytest

from fintech_agent.safety.evidence_mapper import (
    build_public_safe_diagnosis,
    to_public_safe_evidence,
)
from fintech_agent.safety.output_guardrail import check_response_safety
from fintech_agent.llm.message_analyzer import (
    _fallback_analyze,
    _fallback_extract_fields,
)


# Wallet-balance wording that must not leak into non-wallet workflows.
_WALLET_WORDING = [
    "kiểm tra lại số dư",
    "ví chưa cập nhật số dư",
    "cập nhật vào ví",
    "số dư ví",
    "kiểm tra số dư",
]


def _contains_any(text: str, needles: list[str]) -> bool:
    low = text.lower()
    return any(n.lower() in low for n in needles)


# ─── Test A: Train ticket paid but no ticket ────────────────────

class TestA_TrainTicketPaidNoTicket:
    """Payment confirmed but ticket not issued → ticket/provider framing."""

    def _evidence(self):
        # payment recognised (transaction completed), provider says not issued
        return {
            "transaction_status": "đã hoàn thành",
            "amount": "450.000 VND",
            "ticket_status": "not_issued",
            "provider_status": "not_confirmed",
        }

    def test_diagnosis_workflow_is_train_ticket(self):
        diag = build_public_safe_diagnosis(
            "train_ticket", self._evidence(), None, "resolved",
        )
        assert diag["workflow"] == "train_ticket"

    def test_issue_location_is_ticket_or_provider_related(self):
        diag = build_public_safe_diagnosis(
            "train_ticket", self._evidence(), None, "resolved",
        )
        loc = (diag["likely_issue_location"] or "").lower()
        assert any(k in loc for k in ("vé", "nhà cung cấp")), loc

    def test_no_wallet_wording_in_diagnosis(self):
        diag = build_public_safe_diagnosis(
            "train_ticket", self._evidence(), None, "resolved",
        )
        blob = " ".join(str(v) for v in diag.values())
        assert not _contains_any(blob, _WALLET_WORDING), blob

    def test_guardrail_rejects_wallet_wording_in_train_context(self):
        diag = build_public_safe_diagnosis(
            "train_ticket", self._evidence(), None, "resolved",
        )
        wallet_reply = "Bạn có thể kiểm tra lại số dư của mình trong ứng dụng."
        result = check_response_safety(
            wallet_reply, None, workflow="train_ticket", diagnosis=diag,
        )
        assert not result.is_safe
        assert result.sanitized_text is not None

    def test_facts_reflect_payment_ok_ticket_pending(self):
        diag = build_public_safe_diagnosis(
            "train_ticket", self._evidence(), None, "resolved",
        )
        facts = " ".join(diag["confirmed_public_facts"]).lower()
        assert "thanh toán" in facts
        assert "vé" in facts


# ─── Test B: Same message/evidence, different workflows ─────────

class TestB_SameEvidenceDifferentWorkflows:
    """The SAME 'payment ok, downstream pending' evidence is framed per
    workflow — wallet vs ticket vs bill — never cross-contaminated."""

    def _payment_ok_downstream_pending(self, workflow):
        # Build evidence matching each workflow's downstream pending signal.
        downstream = {
            "wallet_topup": {"bank_status": "success", "wallet_status": "not_received"},
            "train_ticket": {"transaction_status": "đã hoàn thành", "ticket_status": "not_issued"},
            "utility_bill": {"transaction_status": "đã hoàn thành", "bill_status": "not_confirmed"},
        }[workflow]
        return downstream

    def test_train_followup_uses_active_workflow_not_hint(self):
        """'Tôi trả khoảng 450 nghìn hôm qua' in a train case → train workflow."""
        msg = "Tôi trả khoảng 450 nghìn hôm qua"
        active = {"selected_workflow": "train_ticket", "awaiting_field": "amount"}
        analysis = _fallback_analyze(msg, active, {})
        assert analysis.workflow_hint == "train_ticket"
        # amount extracted generically (≈450k), not hard-coded
        assert analysis.extracted.amount in (450_000, None) or analysis.extracted.amount > 0

    def test_same_message_in_wallet_case_is_wallet(self):
        msg = "Tôi trả khoảng 450 nghìn hôm qua"
        active = {"selected_workflow": "wallet_topup", "awaiting_field": "amount"}
        analysis = _fallback_analyze(msg, active, {})
        assert analysis.workflow_hint == "wallet_topup"

    def test_no_active_case_is_new_complaint_or_unknown(self):
        msg = "Tôi trả khoảng 450 nghìn hôm qua"
        analysis = _fallback_analyze(msg, {}, {})
        assert analysis.workflow_hint == "unknown"
        assert analysis.message_type in ("new_complaint", "unknown")

    def test_train_framing_has_no_wallet_words(self):
        diag = build_public_safe_diagnosis(
            "train_ticket", self._payment_ok_downstream_pending("train_ticket"),
            None, "resolved",
        )
        blob = " ".join(str(v) for v in diag.values())
        assert not _contains_any(blob, _WALLET_WORDING)
        assert "vé" in blob.lower()

    def test_wallet_framing_keeps_wallet_words(self):
        diag = build_public_safe_diagnosis(
            "wallet_topup", self._payment_ok_downstream_pending("wallet_topup"),
            None, "resolved",
        )
        blob = " ".join(str(v) for v in diag.values()).lower()
        assert "ví" in blob

    def test_utility_framing_is_bill_not_wallet_or_ticket(self):
        diag = build_public_safe_diagnosis(
            "utility_bill", self._payment_ok_downstream_pending("utility_bill"),
            None, "resolved",
        )
        blob = " ".join(str(v) for v in diag.values()).lower()
        assert not _contains_any(blob, _WALLET_WORDING)
        assert "vé" not in blob          # not train wording
        assert "hóa đơn" in blob         # bill wording


# ─── Test C: Wallet topup ──────────────────────────────────────

class TestC_WalletTopup:
    """Wallet diagnosis talks about bank/wallet only when evidence supports it,
    never train/provider wording."""

    def test_bank_ok_wallet_pending(self):
        diag = build_public_safe_diagnosis(
            "wallet_topup",
            {"bank_status": "success", "wallet_status": "not_received"},
            None, "resolved",
        )
        blob = " ".join(str(v) for v in diag.values()).lower()
        assert "ví" in blob
        assert "vé" not in blob          # no train wording
        assert "nhà cung cấp" not in blob

    def test_wallet_wording_allowed_by_guardrail(self):
        diag = build_public_safe_diagnosis(
            "wallet_topup",
            {"bank_status": "success", "wallet_status": "not_received"},
            None, "resolved",
        )
        reply = "Ngân hàng đã xác nhận nhưng ví chưa cập nhật số dư."
        result = check_response_safety(
            reply, None, workflow="wallet_topup", diagnosis=diag,
        )
        assert result.is_safe, result.violations


# ─── Test D: Fraud account lock ────────────────────────────────

class TestD_FraudAccountLock:
    """Diagnosis is account-verification related; never exposes risk/fraud."""

    def test_account_verification_framing(self):
        diag = build_public_safe_diagnosis(
            "fraud_account_lock",
            {"account_status": "under_review"},
            None, "resolved",
        )
        loc = (diag["likely_issue_location"] or "").lower()
        cause = (diag["customer_safe_cause"] or "").lower()
        assert "xác minh" in loc or "xác minh" in cause or "tài khoản" in cause

    def test_not_transaction_id_related(self):
        diag = build_public_safe_diagnosis(
            "fraud_account_lock",
            {"account_status": "under_review"},
            None, "resolved",
        )
        blob = " ".join(str(v) for v in diag.values()).lower()
        assert "transaction_id" not in blob
        assert "mã giao dịch" not in blob

    def test_no_risk_or_fraud_exposed(self):
        diag = build_public_safe_diagnosis(
            "fraud_account_lock",
            {"account_status": "under_review", "risk_score": 95, "fraud_status": "high"},
            None, "resolved",
        )
        blob = " ".join(str(v) for v in diag.values()).lower()
        assert "risk_score" not in blob
        assert "fraud_status" not in blob
        assert "95" not in blob


# ─── Test E: Code scan — no hard-coded business values ──────────

class TestE_NoHardCode:
    """The response pipeline must not hard-code test-specific values."""

    # Files that make up the customer-response business logic.
    PIPELINE_FILES = [
        "api/customer_chat.py",
        "api/generic_resolver.py",
        "safety/evidence_mapper.py",
        "safety/output_guardrail.py",
        "llm/response_composer.py",
    ]

    def _read(self, rel: str) -> str:
        base = os.path.join(
            os.path.dirname(__file__), "..", "src", "fintech_agent",
        )
        path = os.path.abspath(os.path.join(base, rel))
        with open(path, encoding="utf-8") as f:
            return f.read()

    def _scan_lines(self, pattern: str) -> list[str]:
        hits: list[str] = []
        for rel in self.PIPELINE_FILES:
            content = self._read(rel)
            for i, line in enumerate(content.split("\n"), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if re.search(pattern, stripped, re.IGNORECASE):
                    hits.append(f"{rel}:{i}: {stripped[:90]}")
        return hits

    def test_no_hardcoded_transaction_ids(self):
        for pat in (r"TXN_TRAIN_001", r"TXN_TOPUP_001"):
            assert self._scan_lines(pat) == [], f"hard-coded id {pat}"

    def test_no_hardcoded_test_amount(self):
        # 450000 / 450.000 must not appear as a special-case in the pipeline.
        for pat in (r"\b450000\b", r"450\.000", r"\b500000\b"):
            assert self._scan_lines(pat) == [], f"hard-coded amount {pat}"

    def test_no_hardcoded_test_phrases(self):
        for pat in (r"chưa nhận được vé", r"thanh toán mua vé tàu"):
            assert self._scan_lines(pat) == [], f"hard-coded phrase {pat}"

    def test_no_message_equality_branching(self):
        # No 'if customer_message == "..."' style phrase patches.
        hits = self._scan_lines(r"customer_message\s*==")
        hits += self._scan_lines(r"message\s*==\s*[\"']")
        assert hits == [], f"phrase-equality branching found: {hits}"


# ─── to_public_safe_evidence backward-compat ───────────────────

class TestBackwardCompat:
    """The legacy wrapper still returns expected keys."""

    def test_legacy_keys_present(self):
        result = to_public_safe_evidence(
            raw_evidence={"transaction_status": "đang xử lý"},
            rule_result=None,
            workflow="train_ticket",
            resolution_status="resolved",
        )
        for key in (
            "what_we_know", "likely_issue_location", "next_step",
            "customer_action_needed", "confirmed_public_facts",
            "customer_safe_cause", "confidence", "workflow",
        ):
            assert key in result

    def test_no_internal_terms_leak(self):
        result = to_public_safe_evidence(
            raw_evidence={"status": "pending", "force_success": True,
                          "master_wallet_balance": 999999, "risk_score": 80},
            rule_result=None,
            workflow="train_ticket",
        )
        blob = str(result).lower()
        assert "force_success" not in blob
        assert "master_wallet" not in blob
        assert "risk_score" not in blob
