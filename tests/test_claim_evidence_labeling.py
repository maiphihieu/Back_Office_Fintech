"""Tests for claim vs evidence source-labeling and sentence limit enforcement.

Validates that the refactored response composer:
  1. Prefixes verified data with "theo kiểm tra hệ thống"
  2. Does NOT present customer claims as if system confirmed them
  3. Enforces 2-5 sentence limit (soft-allows 6 for safety reminders)
  4. Stale diagnosis is NOT reused when workflow is unknown
  5. Contradiction responses label both sources correctly
"""

import pytest

from fintech_agent.llm.message_analyzer import (
    MessageAnalysis,
    ExtractedFields,
)
from fintech_agent.llm.response_composer import (
    ComposedResponse,
    compose_customer_response,
    _compose_from_diagnosis,
    _enforce_sentence_limit,
)
from fintech_agent.api.customer_claims import (
    CustomerClaims,
    VerifiedEvidence,
    Contradiction,
    detect_contradictions,
)
from fintech_agent.safety.output_guardrail import check_response_safety


# ─── Source Labeling Tests ──────────────────────────────────────


class TestSourceLabeling:
    """Verified evidence must be prefixed with 'theo kiểm tra hệ thống'."""

    def test_verified_cause_prefixed(self):
        """When diagnosis has customer_safe_cause, it must be labeled as system-verified."""
        from fintech_agent.llm.message_analyzer import load_response_policy, get_workflow_policy
        policy = load_response_policy()
        wf_policy = get_workflow_policy("wallet_topup")
        safety_reminder = (
            wf_policy.get("safety_reminder")
            or policy.get("global_safety_reminder", "")
        )

        evidence = {
            "customer_safe_cause": "Giao dịch nạp tiền ghi nhận 500.000đ, đang xử lý.",
            "confirmed_public_facts": ["Giao dịch 500.000đ, trạng thái đang xử lý"],
            "next_step": "Hệ thống đang đối soát với ngân hàng.",
        }
        composed = _compose_from_diagnosis(
            public_safe_evidence=evidence,
            resolution_status="resolved",
            safety_reminder=safety_reminder,
            wf_policy=wf_policy,
            policy=policy,
        )
        msg = composed.public_message.lower()
        # Must use "theo kiểm tra hệ thống" prefix
        assert "theo kiểm tra hệ thống" in msg

    def test_verified_facts_prefixed(self):
        """When only confirmed_public_facts exist, use system label."""
        from fintech_agent.llm.message_analyzer import load_response_policy, get_workflow_policy
        policy = load_response_policy()
        wf_policy = get_workflow_policy("wallet_topup")
        safety_reminder = (
            wf_policy.get("safety_reminder")
            or policy.get("global_safety_reminder", "")
        )

        evidence = {
            "confirmed_public_facts": ["Trạng thái: đang xử lý"],
        }
        composed = _compose_from_diagnosis(
            public_safe_evidence=evidence,
            resolution_status="resolved",
            safety_reminder=safety_reminder,
            wf_policy=wf_policy,
            policy=policy,
        )
        msg = composed.public_message.lower()
        assert "theo kiểm tra hệ thống" in msg

    def test_no_match_does_not_claim_verified(self):
        """When no_match, response must NOT use verified-data language."""
        analysis = MessageAnalysis(
            message_type="new_complaint",
            belongs_to_active_case=False,
            confidence=0.9,
            workflow_hint="wallet_topup",
            extracted=ExtractedFields(amount=300_000),
        )
        composed = compose_customer_response(
            customer_message="tôi nạp 300k mà chưa nhận",
            message_analysis=analysis,
            public_safe_evidence={},
            resolution_status="no_match",
        )
        msg = composed.public_message.lower()
        # Must say not found, not claim it was verified
        assert "chưa tìm thấy" in msg or "chưa có" in msg or "không có" in msg or "chưa ghi nhận" in msg
        # Must NOT say "đã xác nhận" or "đã kiểm tra" (no evidence to confirm)
        assert "đã xác nhận giao dịch" not in msg

    def test_contradiction_labels_both_sources(self):
        """Contradiction notice must have both 'bạn đã đề cập' and verified amount."""
        from fintech_agent.llm.message_analyzer import load_response_policy, get_workflow_policy
        policy = load_response_policy()
        wf_policy = get_workflow_policy("wallet_topup")
        safety_reminder = (
            wf_policy.get("safety_reminder")
            or policy.get("global_safety_reminder", "")
        )

        contradictions = [
            Contradiction(
                field="amount",
                customer_claim=1_000_000,
                verified_value=500_000,
                severity="medium",
            )
        ]
        evidence = {
            "customer_safe_cause": "Giao dịch nạp tiền ghi nhận 500.000đ, đang xử lý.",
            "confirmed_public_facts": ["Giao dịch 500.000đ, trạng thái đang xử lý"],
        }
        composed = _compose_from_diagnosis(
            public_safe_evidence=evidence,
            resolution_status="resolved",
            safety_reminder=safety_reminder,
            wf_policy=wf_policy,
            policy=policy,
            contradictions=contradictions,
        )
        msg = composed.public_message.lower()
        # Both amounts must be present
        assert "500.000" in msg
        assert "1.000.000" in msg
        # Customer's claim labeled
        assert "đã đề cập" in msg or "bạn cung cấp" in msg


# ─── Sentence Limit Tests ──────────────────────────────────────


class TestSentenceLimit:
    """Every composed response must have 2-5 sentences (soft 6 for safety)."""

    def test_enforce_under_limit(self):
        """3 sentences → unchanged."""
        text = "Câu 1. Câu 2. Câu 3."
        result = _enforce_sentence_limit(text)
        assert result == text

    def test_enforce_at_limit(self):
        """5 sentences → unchanged."""
        text = "A. B. C. D. E."
        result = _enforce_sentence_limit(text)
        assert result == text

    def test_enforce_over_limit_truncated(self):
        """7 sentences without safety → truncated to 5."""
        text = "A. B. C. D. E. F. G."
        result = _enforce_sentence_limit(text)
        # Count sentences
        sentences = [s.strip() for s in result.split(". ") if s.strip()]
        # The "." at the end might give an empty split, so filter
        assert len(sentences) <= 5

    def test_safety_reminder_allows_6(self):
        """6th sentence with PIN/OTP keyword → allowed."""
        text = "A. B. C. D. E. Vui lòng không gửi mã PIN hoặc OTP."
        result = _enforce_sentence_limit(text)
        assert "pin" in result.lower() or "otp" in result.lower()

    def test_empty_text_unchanged(self):
        """Empty text → unchanged."""
        assert _enforce_sentence_limit("") == ""
        assert _enforce_sentence_limit("   ") == "   "

    def test_composed_response_within_limit(self):
        """Full compose pipeline produces a response within sentence limit."""
        analysis = MessageAnalysis(
            message_type="new_complaint",
            belongs_to_active_case=False,
            confidence=0.9,
            workflow_hint="wallet_topup",
            extracted=ExtractedFields(amount=500_000),
        )
        evidence = {
            "customer_safe_cause": "Giao dịch đang chờ xử lý.",
            "confirmed_public_facts": ["Trạng thái: đang xử lý"],
            "next_step": "Hệ thống đang đối soát.",
        }
        composed = compose_customer_response(
            customer_message="tôi nạp 500k chưa nhận",
            message_analysis=analysis,
            public_safe_evidence=evidence,
            resolution_status="resolved",
        )
        # Count sentences (split on sentence-ending punctuation)
        import re
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", composed.public_message) if s.strip()]
        # Allow up to 6 (safety extension)
        assert len(sentences) <= 6, (
            f"Response has {len(sentences)} sentences: {composed.public_message}"
        )


# ─── Stale Diagnosis Guard Tests ────────────────────────────────


class TestStaleDiagnosisGuardTightened:
    """Verify the tightened guard doesn't reuse diagnosis for unknown workflow."""

    def test_unknown_workflow_gets_fresh_response(self):
        """When workflow is unknown, composer must NOT inherit stale diagnosis wording.

        In production, the stale guard in customer_chat.py now discards the
        diagnosis when _current_wf is empty/unknown. Here we verify that the
        deterministic composer produces a reasonable response without a diagnosis.
        """
        analysis = MessageAnalysis(
            message_type="follow_up",
            belongs_to_active_case=True,
            confidence=0.5,
            workflow_hint="",  # unknown
            extracted=ExtractedFields(),
        )
        # No evidence at all — simulates the case where diagnosis was discarded
        composed = compose_customer_response(
            customer_message="kiểm tra lại đi",
            message_analysis=analysis,
            public_safe_evidence={},
            resolution_status="",
        )
        # Should produce a generic response, not crash
        assert composed.public_message
        assert len(composed.public_message) > 10
        # Should NOT contain stale wallet-topup wording
        assert "nạp ví" not in composed.public_message.lower()


# ─── Compose Entry Point: claims/evidence passthrough ──────────


class TestClaimsEvidencePassthrough:
    """compose_customer_response accepts and passes through claims/evidence."""

    def test_accepts_claims_and_evidence_params(self):
        """No error when customer_claims and verified_evidence are passed."""
        analysis = MessageAnalysis(
            message_type="provide_missing_info",
            workflow_hint="wallet_topup",
        )
        claims = {"amount": 500_000, "bank_name": "VCB"}
        evidence = {"fields": [{"field": "amount", "value": 500_000, "source": "transaction_table"}]}

        composed = compose_customer_response(
            customer_message="tôi nạp 500k qua VCB",
            message_analysis=analysis,
            public_safe_evidence={
                "customer_safe_cause": "Giao dịch ghi nhận 500.000đ.",
            },
            resolution_status="resolved",
            customer_claims=claims,
            verified_evidence=evidence,
        )
        assert composed.public_message
        assert len(composed.public_message) > 10

    def test_works_without_claims_and_evidence(self):
        """Backward compatible — works without new params (all None)."""
        analysis = MessageAnalysis(
            message_type="provide_missing_info",
            workflow_hint="wallet_topup",
        )
        composed = compose_customer_response(
            customer_message="tôi nạp 500k qua VCB",
            message_analysis=analysis,
            public_safe_evidence={
                "customer_safe_cause": "Giao dịch ghi nhận 500.000đ.",
            },
            resolution_status="resolved",
        )
        assert composed.public_message
        assert len(composed.public_message) > 10
