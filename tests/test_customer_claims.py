"""Tests for the customer_claims module — claim/evidence separation.

Tests verify:
  1. CustomerClaims tracks and accumulates claims.
  2. Corrections (is_correction=True) supersede older claims.
  3. VerifiedEvidence captures DB-resolved data.
  4. detect_contradictions finds mismatches between claims and evidence.
  5. get_unverified_claims returns claims not yet verified.
"""

import pytest
from fintech_agent.api.customer_claims import (
    ClaimRecord,
    CustomerClaims,
    VerifiedEvidence,
    Contradiction,
    detect_contradictions,
    get_unverified_claims,
)
from fintech_agent.llm.message_analyzer import ExtractedFields


# ─── CustomerClaims Tests ──────────────────────────────────────

class TestCustomerClaims:
    """Test claim tracking and accumulation."""

    def test_merge_amount_claim(self):
        claims = CustomerClaims()
        ef = ExtractedFields(amount=500000)
        claims.merge_extracted_fields(ef, is_correction=False)
        assert claims.latest["amount"] == 500000
        assert len(claims.history) == 1
        assert claims.history[0].field == "amount"
        assert claims.history[0].value == 500000
        assert claims.history[0].superseded is False

    def test_merge_multiple_fields(self):
        claims = CustomerClaims()
        ef = ExtractedFields(amount=300000, bank_name="VCB")
        claims.merge_extracted_fields(ef, is_correction=False)
        assert claims.latest["amount"] == 300000
        assert claims.latest["bank_name"] == "VCB"
        assert len(claims.history) == 2

    def test_correction_supersedes(self):
        claims = CustomerClaims()
        # First claim
        ef1 = ExtractedFields(amount=500000)
        claims.merge_extracted_fields(ef1, is_correction=False)
        assert claims.latest["amount"] == 500000

        # Correction
        ef2 = ExtractedFields(amount=300000)
        claims.merge_extracted_fields(ef2, is_correction=True)
        assert claims.latest["amount"] == 300000
        # Old claim should be superseded
        old_claims = [
            c for c in claims.history
            if c.field == "amount" and c.value == 500000
        ]
        assert len(old_claims) == 1
        assert old_claims[0].superseded is True

    def test_accumulate_without_overwrite(self):
        """New field value only overwrites if non-empty."""
        claims = CustomerClaims()
        ef1 = ExtractedFields(amount=500000)
        claims.merge_extracted_fields(ef1, is_correction=False)

        # Second message with NO amount but with bank
        ef2 = ExtractedFields(bank_name="TCB")
        claims.merge_extracted_fields(ef2, is_correction=False)

        # Amount should remain from first message
        assert claims.latest["amount"] == 500000
        assert claims.latest["bank_name"] == "TCB"

    def test_empty_extraction_does_nothing(self):
        claims = CustomerClaims()
        ef = ExtractedFields()
        claims.merge_extracted_fields(ef, is_correction=False)
        assert len(claims.history) == 0

    def test_to_summary(self):
        """to_summary() serializes claims for API."""
        claims = CustomerClaims()
        ef = ExtractedFields(amount=500000, bank_name="VCB")
        claims.merge_extracted_fields(ef, is_correction=False)
        summary = claims.to_summary()
        assert len(summary) == 2
        fields = {s["field"] for s in summary}
        assert "amount" in fields
        assert "bank_name" in fields


# ─── Contradiction Detection Tests ─────────────────────────────

class TestContradictionDetection:
    """Test detect_contradictions between claims and evidence."""

    def test_amount_contradiction(self):
        claims = CustomerClaims()
        claims.merge_extracted_fields(
            ExtractedFields(amount=500000), is_correction=False,
        )
        evidence = VerifiedEvidence(verified_amount=300000)
        contradictions = detect_contradictions(claims, evidence)
        assert len(contradictions) == 1
        assert contradictions[0].field == "amount"
        assert contradictions[0].customer_claim == 500000
        assert contradictions[0].verified_value == 300000

    def test_bank_name_contradiction(self):
        claims = CustomerClaims()
        claims.merge_extracted_fields(
            ExtractedFields(bank_name="VCB"), is_correction=False,
        )
        evidence = VerifiedEvidence(verified_bank_name="TCB")
        contradictions = detect_contradictions(claims, evidence)
        assert len(contradictions) == 1
        assert contradictions[0].field == "bank_name"

    def test_no_contradiction_when_matching(self):
        claims = CustomerClaims()
        claims.merge_extracted_fields(
            ExtractedFields(amount=500000, bank_name="VCB"),
            is_correction=False,
        )
        evidence = VerifiedEvidence(
            verified_amount=500000, verified_bank_name="VCB",
        )
        contradictions = detect_contradictions(claims, evidence)
        assert len(contradictions) == 0

    def test_no_contradiction_when_no_evidence(self):
        """If evidence has no verified data, no contradictions."""
        claims = CustomerClaims()
        claims.merge_extracted_fields(
            ExtractedFields(amount=500000), is_correction=False,
        )
        evidence = VerifiedEvidence()
        contradictions = detect_contradictions(claims, evidence)
        assert len(contradictions) == 0

    def test_large_amount_difference_is_contradiction(self):
        """Large amount difference should be a contradiction."""
        claims = CustomerClaims()
        claims.merge_extracted_fields(
            ExtractedFields(amount=1000000), is_correction=False,
        )
        evidence = VerifiedEvidence(verified_amount=500000)
        contradictions = detect_contradictions(claims, evidence)
        assert len(contradictions) == 1
        assert contradictions[0].field == "amount"


# ─── Unverified Claims Tests ──────────────────────────────────

class TestGetUnverifiedClaims:
    """Test get_unverified_claims returns claims not yet verified."""

    def test_all_unverified(self):
        claims = CustomerClaims()
        claims.merge_extracted_fields(
            ExtractedFields(amount=500000, bank_name="VCB"),
            is_correction=False,
        )
        evidence = VerifiedEvidence()
        unverified = get_unverified_claims(claims, evidence)
        fields = [u["field"] for u in unverified]
        assert "amount" in fields
        assert "bank_name" in fields

    def test_partial_verified(self):
        claims = CustomerClaims()
        claims.merge_extracted_fields(
            ExtractedFields(amount=500000, bank_name="VCB"),
            is_correction=False,
        )
        evidence = VerifiedEvidence(verified_amount=500000)
        unverified = get_unverified_claims(claims, evidence)
        fields = [u["field"] for u in unverified]
        assert "amount" not in fields
        assert "bank_name" in fields

    def test_all_verified(self):
        claims = CustomerClaims()
        claims.merge_extracted_fields(
            ExtractedFields(amount=500000, bank_name="VCB"),
            is_correction=False,
        )
        evidence = VerifiedEvidence(
            verified_amount=500000, verified_bank_name="VCB",
        )
        unverified = get_unverified_claims(claims, evidence)
        assert len(unverified) == 0
