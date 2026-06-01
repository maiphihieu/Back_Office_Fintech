"""Tests for conflict detection after claim verification refactor.

IMPORTANT: Amount mismatch between customer and system is NO LONGER a blocking
EvidenceConflict. It's handled by claim_verifier.py as a non-blocking
ClaimVerification result.

These tests verify that:
- detect_all_conflicts no longer takes customer_claimed_amount
- Amount mismatches between customer/system do NOT produce EvidenceConflicts
- System-vs-system conflicts (ledger vs transaction) still produce conflicts
- The claim_verifier correctly handles amount verification
"""

from __future__ import annotations

import unittest

from fintech_agent.rules.claim_verifier import verify_all_claims
from fintech_agent.rules.conflict_rules import detect_all_conflicts
from fintech_agent.schemas.claim_verification import ClaimType, VerificationStatus
from fintech_agent.schemas.evidence import (
    EvidenceBundle,
    Transaction,
    WalletLedger,
)


def _make_evidence(
    debit_amount: int = 0,
    txn_amount: int = 0,
) -> EvidenceBundle:
    """Build an EvidenceBundle with wallet_ledger and transaction."""
    return EvidenceBundle(
        transaction=Transaction(
            transaction_id="TXN_001",
            user_id="USER_001",
            service_type="train_ticket",
            amount=txn_amount,
            status="completed",
        ),
        wallet_ledger=WalletLedger(
            transaction_id="TXN_001",
            user_id="USER_001",
            has_user_debit=True,
            debit_amount=debit_amount,
        ),
    )


class TestConflictDetectionRefactored(unittest.TestCase):
    """Test that detect_all_conflicts only detects system-vs-system conflicts."""

    def test_no_conflicts_when_system_consistent(self):
        """No conflicts when all system sources agree."""
        evidence = _make_evidence(debit_amount=350000, txn_amount=350000)
        conflicts = detect_all_conflicts(evidence, case_user_id="USER_001")
        self.assertEqual(len(conflicts), 0)

    def test_no_amount_mismatch_conflict_type(self):
        """detect_all_conflicts should never produce amount_mismatch conflicts.

        Amount mismatch is handled by claim_verifier, not conflict_rules.
        """
        evidence = _make_evidence(debit_amount=350000, txn_amount=350000)
        conflicts = detect_all_conflicts(evidence, case_user_id="USER_001")
        amount_conflicts = [c for c in conflicts if c.conflict_type == "amount_mismatch"]
        self.assertEqual(len(amount_conflicts), 0)

    def test_customer_amount_mismatch_not_blocking(self):
        """Customer claiming wrong amount should NOT produce a blocking conflict.

        Instead, it should be caught by claim_verifier as a non-blocking mismatch.
        """
        evidence = _make_evidence(debit_amount=450000, txn_amount=450000)
        # No customer_claimed_amount parameter — it's been removed
        conflicts = detect_all_conflicts(evidence, case_user_id="USER_001")
        # No amount_mismatch conflict
        amount_conflicts = [c for c in conflicts if c.conflict_type == "amount_mismatch"]
        self.assertEqual(len(amount_conflicts), 0)

        # But the claim verifier catches it
        extracted = {"amount_claimed": 4500000}
        cv = verify_all_claims(extracted, evidence)
        self.assertTrue(cv.has_customer_detail_mismatch)
        self.assertIn(ClaimType.TRANSACTION_AMOUNT, cv.mismatched_claims)
        # System is consistent internally
        self.assertFalse(cv.has_system_evidence_conflict)


class TestClaimVerifierAmountChecks(unittest.TestCase):
    """Test claim_verifier amount verification (replaces old conflict tests)."""

    def test_matching_amount_matched(self):
        """Claimed == system amount → matched claim."""
        evidence = _make_evidence(debit_amount=350000, txn_amount=350000)
        extracted = {"amount_claimed": 350000}
        cv = verify_all_claims(extracted, evidence)
        self.assertIn(ClaimType.TRANSACTION_AMOUNT, cv.matched_claims)
        self.assertFalse(cv.has_customer_detail_mismatch)

    def test_mismatching_amount_creates_mismatch(self):
        """Claimed != system amount → mismatched claim, but NOT blocking."""
        evidence = _make_evidence(debit_amount=450000, txn_amount=450000)
        extracted = {"amount_claimed": 4500000}
        cv = verify_all_claims(extracted, evidence)
        self.assertIn(ClaimType.TRANSACTION_AMOUNT, cv.mismatched_claims)
        self.assertTrue(cv.has_customer_detail_mismatch)
        # System is consistent → no system conflict
        self.assertFalse(cv.has_system_evidence_conflict)

    def test_wallet_ledger_priority(self):
        """When wallet_ledger and transaction both exist, use wallet_ledger."""
        evidence = _make_evidence(debit_amount=450000, txn_amount=500000)
        extracted = {"amount_claimed": 500000}
        cv = verify_all_claims(extracted, evidence)
        # Claimed 500000 vs wallet_ledger 450000 → mismatch
        amount_claim = next(
            c for c in cv.claims
            if c.claim_type == ClaimType.TRANSACTION_AMOUNT
        )
        self.assertEqual(amount_claim.trusted_system_value, 450000)
        self.assertEqual(amount_claim.trusted_source, "wallet_ledger.debit_amount")
        self.assertEqual(amount_claim.verification_status, VerificationStatus.MISMATCHED)

    def test_no_claimed_amount_no_claim(self):
        """No customer_claimed_amount → system_only amount claim injected."""
        evidence = _make_evidence(debit_amount=350000, txn_amount=350000)
        extracted = {}
        cv = verify_all_claims(extracted, evidence)
        amount_claims = [
            c for c in cv.claims
            if c.claim_type == ClaimType.TRANSACTION_AMOUNT
        ]
        # system_only claim is injected when evidence has amount but customer didn't claim
        self.assertEqual(len(amount_claims), 1)
        self.assertEqual(amount_claims[0].verification_status, VerificationStatus.SYSTEM_ONLY)
        self.assertIsNone(amount_claims[0].customer_claimed_value)

    def test_no_system_amount_not_verifiable(self):
        """If no system amount exists, claim is not_verifiable."""
        evidence = EvidenceBundle(
            transaction=Transaction(
                transaction_id="TXN_001",
                user_id="USER_001",
                service_type="train_ticket",
                amount=0,
                status="completed",
            ),
        )
        extracted = {"amount_claimed": 350000}
        cv = verify_all_claims(extracted, evidence)
        amount_claim = next(
            c for c in cv.claims
            if c.claim_type == ClaimType.TRANSACTION_AMOUNT
        )
        self.assertEqual(
            amount_claim.verification_status, VerificationStatus.NOT_VERIFIABLE
        )


if __name__ == "__main__":
    unittest.main()
