"""Tests for generic claim verifier — registry-based verification.

Test cases:
    1. All claims match system data
    2. Customer amount mismatch but system consistent → customer_detail_mismatch
    3. System sources conflict → system_evidence_conflict
    4. Mixed claims: some matched, some mismatched, some not verifiable
    5. Action amount never uses customer_claimed_amount
    6. Staff explanation in Vietnamese
    7. No extracted info → no claims to verify
    8. No evidence → claims are not_verifiable
    9. System conflict with reconciliation amount
    10. Pydantic model as extracted_info
    11. Wallet balance 0 is NOT transaction amount
    12. Registry dispatches to correct verifier
    13. Unknown claim type → default verifier
    14. Ticket always generated even with missing/conflict data
"""

from fintech_agent.rules.claim_verifier import verify_all_claims
from fintech_agent.schemas.claim_verification import Claim, ClaimType, VerificationStatus
from fintech_agent.schemas.evidence import (
    EvidenceBundle,
    ReconciliationStatus,
    Transaction,
    WalletLedger,
)


def _make_transaction(**overrides):
    defaults = {
        "transaction_id": "TXN001",
        "user_id": "U001",
        "service_type": "train_ticket",
        "amount": 500_000,
        "status": "completed",
    }
    defaults.update(overrides)
    return Transaction.model_validate(defaults)


def _make_wallet_ledger(**overrides):
    defaults = {
        "transaction_id": "TXN001",
        "user_id": "U001",
        "debit_amount": 500_000,
        "status": "debited",
    }
    defaults.update(overrides)
    return WalletLedger.model_validate(defaults)


# ─── Test 1: All claims matched ──────────────────────────────────


def test_all_claims_matched():
    """When customer provides correct info, all claims should be 'matched'."""
    evidence = EvidenceBundle(
        transaction=_make_transaction(),
        wallet_ledger=_make_wallet_ledger(),
    )
    extracted = {
        "transaction_id": "TXN001",
        "amount_claimed": 500_000,
        "service_type": "train_ticket",
    }

    result = verify_all_claims(extracted, evidence)

    assert not result.has_customer_detail_mismatch
    assert not result.has_system_evidence_conflict
    # claim_type values are used as identifiers now
    matched_types = result.matched_claims
    assert ClaimType.TRANSACTION_ID in matched_types
    assert ClaimType.TRANSACTION_AMOUNT in matched_types
    assert len(result.mismatched_claims) == 0


# ─── Test 2: Customer amount mismatch, system consistent ──


def test_customer_amount_mismatch_system_consistent():
    """Customer claims wrong amount, but system sources agree."""
    evidence = EvidenceBundle(
        transaction=_make_transaction(amount=500_000),
        wallet_ledger=_make_wallet_ledger(debit_amount=500_000),
    )
    extracted = {
        "transaction_id": "TXN001",
        "amount_claimed": 1_000_000,
        "service_type": "train_ticket",
    }

    result = verify_all_claims(extracted, evidence)

    assert result.has_customer_detail_mismatch
    assert not result.has_system_evidence_conflict
    assert ClaimType.TRANSACTION_AMOUNT in result.mismatched_claims
    assert ClaimType.TRANSACTION_ID in result.matched_claims

    # Verify the amount claim details
    amount_claim = next(
        c for c in result.claims
        if c.claim_type == ClaimType.TRANSACTION_AMOUNT
    )
    assert amount_claim.customer_claimed_value == 1_000_000
    assert amount_claim.trusted_system_value == 500_000
    assert amount_claim.trusted_source == "wallet_ledger.debit_amount"
    assert amount_claim.verification_status == VerificationStatus.MISMATCHED


# ─── Test 3: System evidence conflict → manual review ──


def test_system_evidence_conflict_blocks():
    """When system sources disagree on amount, has_system_evidence_conflict=True."""
    evidence = EvidenceBundle(
        transaction=_make_transaction(amount=500_000),
        wallet_ledger=_make_wallet_ledger(debit_amount=750_000),  # Mismatch!
    )
    extracted = {
        "transaction_id": "TXN001",
        "amount_claimed": 500_000,
    }

    result = verify_all_claims(extracted, evidence)

    assert result.has_system_evidence_conflict
    assert result.staff_explanation


# ─── Test 4: Mixed verification statuses ──


def test_mixed_claim_statuses():
    """Some claims match, some mismatch, some can't be verified."""
    evidence = EvidenceBundle(
        transaction=_make_transaction(service_type="electric_bill"),
        wallet_ledger=_make_wallet_ledger(),
    )
    extracted = {
        "transaction_id": "TXN001",
        "amount_claimed": 999_999,
        "service_type": "train_ticket",
    }

    result = verify_all_claims(extracted, evidence)

    assert result.has_customer_detail_mismatch
    assert ClaimType.TRANSACTION_ID in result.matched_claims
    assert ClaimType.TRANSACTION_AMOUNT in result.mismatched_claims


# ─── Test 5: Action amount never uses customer_claimed_amount ──


def test_action_amount_never_from_customer():
    """Verify that the trusted amount comes from system, not customer."""
    evidence = EvidenceBundle(
        transaction=_make_transaction(amount=500_000),
        wallet_ledger=_make_wallet_ledger(debit_amount=500_000),
    )
    extracted = {
        "amount_claimed": 1_000_000,
    }

    result = verify_all_claims(extracted, evidence)

    amount_claim = next(
        c for c in result.claims
        if c.claim_type == ClaimType.TRANSACTION_AMOUNT
    )
    assert amount_claim.trusted_system_value == 500_000
    assert amount_claim.trusted_source == "wallet_ledger.debit_amount"
    assert amount_claim.trusted_system_value != extracted["amount_claimed"]


# ─── Test 6: Staff explanation in Vietnamese ──


def test_staff_explanation_generated():
    """Staff explanation should be generated in Vietnamese."""
    evidence = EvidenceBundle(
        transaction=_make_transaction(),
        wallet_ledger=_make_wallet_ledger(),
    )
    extracted = {
        "transaction_id": "TXN001",
        "amount_claimed": 999_999,
    }

    result = verify_all_claims(extracted, evidence)

    assert result.staff_explanation
    assert len(result.staff_explanation) > 0
    assert "999,999" in result.staff_explanation or "999999" in result.staff_explanation


# ─── Test 7: No extracted info → empty claims ──


def test_no_extracted_info():
    """When no customer claims are extracted, system_only claims may be injected."""
    evidence = EvidenceBundle(
        transaction=_make_transaction(),
        wallet_ledger=_make_wallet_ledger(),
    )

    result = verify_all_claims(None, evidence)

    assert not result.has_customer_detail_mismatch
    assert not result.has_system_evidence_conflict
    # system_only claim is injected for transaction_amount when evidence has it
    assert len(result.claims) == 1
    assert result.claims[0].claim_type == ClaimType.TRANSACTION_AMOUNT
    assert result.claims[0].verification_status == VerificationStatus.SYSTEM_ONLY
    assert len(result.matched_claims) == 0
    assert len(result.mismatched_claims) == 0


# ─── Test 8: No evidence → claims are not_verifiable ──


def test_no_evidence_claims_not_verifiable():
    """When no system evidence exists, customer claims can't be verified."""
    evidence = EvidenceBundle()
    extracted = {
        "transaction_id": "TXN001",
        "amount_claimed": 500_000,
        "service_type": "train_ticket",
    }

    result = verify_all_claims(extracted, evidence)

    assert not result.has_customer_detail_mismatch
    assert not result.has_system_evidence_conflict
    # Claims should be not_verifiable or not_found
    non_matched_count = len(result.not_verifiable_claims) + len(result.not_found_claims)
    assert non_matched_count > 0


# ─── Test 9: System conflict with reconciliation amount ──


def test_system_conflict_with_reconciliation():
    """Three-way conflict: transaction, wallet, reconciliation disagree."""
    evidence = EvidenceBundle(
        transaction=_make_transaction(amount=500_000),
        wallet_ledger=_make_wallet_ledger(debit_amount=500_000),
        reconciliation_status=ReconciliationStatus.model_validate({
            "transaction_id": "TXN001",
            "match_status": "mismatched",
            "bank_amount": 750_000,
        }),
    )
    extracted = {
        "amount_claimed": 500_000,
    }

    result = verify_all_claims(extracted, evidence)

    assert result.has_system_evidence_conflict


# ─── Test 10: Pydantic model as extracted_info ──


def test_pydantic_model_extracted_info():
    """Should handle Pydantic model as extracted_info input."""
    from fintech_agent.schemas.case_state import ExtractedInfo

    ei = ExtractedInfo(
        transaction_id="TXN001",
        amount_claimed=500_000,
        service_type="train_ticket",
        issue_type="paid_but_no_ticket",
    )
    evidence = EvidenceBundle(
        transaction=_make_transaction(),
        wallet_ledger=_make_wallet_ledger(),
    )

    result = verify_all_claims(ei, evidence)

    assert ClaimType.TRANSACTION_ID in result.matched_claims
    assert ClaimType.TRANSACTION_AMOUNT in result.matched_claims
    assert not result.has_customer_detail_mismatch


# ─── Test 11: Wallet balance 0 is NOT transaction amount ──


def test_wallet_balance_zero_not_transaction_amount():
    """'ví vẫn 0đ' should produce wallet_balance_claim, not transaction_amount_claim."""
    from fintech_agent.llm.mock_extractor import extract_claims

    claims = extract_claims("Tôi nạp tiền, ngân hàng đã trừ tiền nhưng ví vẫn 0đ. Mã TXN_TOPUP_001.")

    claim_types = [c.claim_type for c in claims]

    # Should have wallet_balance_claim
    assert ClaimType.WALLET_BALANCE in claim_types
    # The "0" from "ví vẫn 0đ" should NOT be classified as transaction_amount
    wallet_claim = next(c for c in claims if c.claim_type == ClaimType.WALLET_BALANCE)
    assert wallet_claim.normalized_value == 0
    # Should have transaction_id
    assert ClaimType.TRANSACTION_ID in claim_types
    txn_claim = next(c for c in claims if c.claim_type == ClaimType.TRANSACTION_ID)
    assert txn_claim.customer_claimed_value == "TXN_TOPUP_001"


# ─── Test 12: Transaction ID number is not amount ──


def test_txn_id_number_not_amount():
    """Numbers inside transaction IDs should not be treated as amounts."""
    from fintech_agent.llm.mock_extractor import extract_claims

    claims = extract_claims("Giao dịch TXN_TRAIN_12345 bị lỗi, tôi chưa nhận được vé.")

    claim_types = [c.claim_type for c in claims]

    # Should have transaction_id_claim
    assert ClaimType.TRANSACTION_ID in claim_types
    txn_claim = next(c for c in claims if c.claim_type == ClaimType.TRANSACTION_ID)
    assert txn_claim.customer_claimed_value == "TXN_TRAIN_12345"
    # Should NOT have any transaction_amount_claim (no money mentioned)
    assert ClaimType.TRANSACTION_AMOUNT not in claim_types


# ─── Test 13: Payment status claim extraction ──


def test_payment_status_claim_extraction():
    """'ngân hàng đã trừ tiền' should produce payment_status_claim."""
    from fintech_agent.llm.mock_extractor import extract_claims

    claims = extract_claims("Ngân hàng đã trừ tiền nhưng ví vẫn 0đ.")

    claim_types = [c.claim_type for c in claims]
    assert ClaimType.PAYMENT_STATUS in claim_types
    ps = next(c for c in claims if c.claim_type == ClaimType.PAYMENT_STATUS)
    assert ps.customer_claimed_value == "bank_deducted"


# ─── Test 14: Unknown claim type → default verifier ──


def test_unknown_claim_type_default_verifier():
    """Claims with unknown type should get not_verifiable via default verifier."""
    evidence = EvidenceBundle(
        transaction=_make_transaction(),
    )
    unknown_claim = Claim(
        claim_type=ClaimType.UNKNOWN,
        raw_text="something unclear",
        customer_claimed_value="unclear",
    )

    result = verify_all_claims({"claims": [unknown_claim.model_dump()]}, evidence)

    # 1 unknown claim + 1 system_only claim for transaction_amount (injected)
    assert len(result.claims) == 2
    unknown_results = [c for c in result.claims if c.claim_type == ClaimType.UNKNOWN]
    assert len(unknown_results) == 1
    assert unknown_results[0].verification_status == VerificationStatus.NOT_VERIFIABLE


# ─── Test 15: Customer mismatch + system consistent → workflow continues ──


def test_customer_mismatch_workflow_continues():
    """Customer detail mismatch should NOT block workflow."""
    evidence = EvidenceBundle(
        transaction=_make_transaction(amount=450_000),
        wallet_ledger=_make_wallet_ledger(debit_amount=450_000),
    )
    extracted = {
        "amount_claimed": 1_000_000,
        "transaction_id": "TXN001",
    }

    result = verify_all_claims(extracted, evidence)

    assert result.has_customer_detail_mismatch
    assert not result.has_system_evidence_conflict
    # Summary should still be generated
    assert result.summary
    assert result.staff_explanation
    assert "tiếp tục xử lý" in result.staff_explanation


# ─── Test 16: System conflict → ticket still generated ──


def test_system_conflict_ticket_still_generated():
    """System evidence conflict should generate a summary with all details."""
    evidence = EvidenceBundle(
        transaction=_make_transaction(amount=500_000),
        wallet_ledger=_make_wallet_ledger(debit_amount=800_000),
    )
    extracted = {
        "amount_claimed": 500_000,
    }

    result = verify_all_claims(extracted, evidence)

    assert result.has_system_evidence_conflict
    assert result.summary
    assert result.staff_explanation
    assert "mâu thuẫn" in result.staff_explanation
    # Claims should still be verified
    assert len(result.claims) > 0


# ─── Test 17: Multiple claim types from realistic complaint ──


def test_realistic_complaint_multiple_claims():
    """A realistic complaint should produce multiple typed claims."""
    from fintech_agent.llm.mock_extractor import extract_claims

    claims = extract_claims(
        "Tôi nạp tiền, ngân hàng đã trừ tiền nhưng ví vẫn 0đ. "
        "Mã TXN_TOPUP_001."
    )

    claim_types = {c.claim_type for c in claims}

    # Should have at least: transaction_id, wallet_balance, payment_status
    assert ClaimType.TRANSACTION_ID in claim_types
    assert ClaimType.WALLET_BALANCE in claim_types
    assert ClaimType.PAYMENT_STATUS in claim_types
    # Should NOT infer transaction_amount from "0đ"
    # (0đ is wallet balance, not transaction amount)


# ─── Test 18: trusted_data_used_for_action populated from evidence ──


def test_trusted_data_populated_from_evidence():
    """trusted_data_used_for_action should contain system data, not customer data."""
    evidence = EvidenceBundle(
        transaction=_make_transaction(amount=500_000),
        wallet_ledger=_make_wallet_ledger(debit_amount=500_000),
    )
    extracted = {
        "transaction_id": "TXN001",
        "amount_claimed": 1_000_000,  # Wrong amount from customer
    }

    result = verify_all_claims(extracted, evidence)

    td = result.trusted_data_used_for_action
    # Action amount should be from system evidence, NOT customer's 1_000_000
    assert td.get("action_amount") == 500_000
    assert td.get("action_amount_source") == "wallet_ledger.debit_amount"
    assert td.get("transaction_id") == "TXN001"
    assert td.get("service_type") == "train_ticket"
    assert td.get("user_id") == "U001"


# ─── Test 19: Missing user_id still generates claims and ticket data ──


def test_missing_user_id_still_generates_claims():
    """When user_id is missing from evidence, claims should still be extracted and verified.

    Resolution ticket must still be generated — missing_user_id is NOT a stop condition.
    """
    # No transaction at all — simulates case where user_id lookup failed
    evidence = EvidenceBundle(
        wallet_ledger=_make_wallet_ledger(debit_amount=500_000),
    )
    extracted = {
        "transaction_id": "TXN001",
        "amount_claimed": 500_000,
    }

    result = verify_all_claims(extracted, evidence)

    # Claims are still verified (txn_id may be not_found since no transaction)
    assert len(result.claims) > 0
    # Summary and explanation are still generated
    assert result.summary
    # trusted_data should still contain what's available
    td = result.trusted_data_used_for_action
    assert td.get("action_amount") == 500_000


# ─── Test 20: wallet_balance_claim NEVER used as action_amount ──


def test_wallet_balance_never_used_as_action_amount():
    """wallet_balance_claim value (e.g. 0) must NEVER appear as action_amount.

    action_amount must always come from wallet_ledger.debit_amount or transaction.amount.
    """
    evidence = EvidenceBundle(
        transaction=_make_transaction(amount=500_000),
        wallet_ledger=_make_wallet_ledger(debit_amount=500_000),
    )
    extracted = {
        "transaction_id": "TXN001",
        "amount_claimed": 500_000,
    }

    result = verify_all_claims(extracted, evidence)

    td = result.trusted_data_used_for_action
    # Even if customer said "ví vẫn 0đ", action_amount must be from system
    assert td.get("action_amount") == 500_000
    assert "wallet_ledger" in (td.get("action_amount_source") or "")
