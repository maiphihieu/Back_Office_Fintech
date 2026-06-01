"""Tests for wallet balance claim bug fix.

Bug: "ví vẫn hiển thị 0đ" was being displayed as:
     "Số tiền giao dịch: khách khai 0đ → hệ thống 500.000đ"
     This is wrong — 0đ is wallet balance, NOT transaction amount.

Fix:
    1. "ví vẫn 0đ" → wallet_balance_claim, never transaction_amount_claim
    2. amount_claimed field is NOT set from wallet-balance context
    3. If customer didn't provide transaction amount but system has one,
       verification_status = system_only (not mismatch)
    4. Action amount still uses trusted system evidence (transaction.amount)
"""

from fintech_agent.llm.mock_extractor import extract_claims, mock_extract
from fintech_agent.rules.claim_verifier import verify_all_claims
from fintech_agent.schemas.claim_verification import (
    Claim,
    ClaimType,
    VerificationStatus,
)
from fintech_agent.schemas.evidence import (
    EvidenceBundle,
    ReconciliationStatus,
    Transaction,
    WalletLedger,
)


# ─── Helpers ────────────────────────────────────────────────────

def _topup_complaint():
    return (
        "Tôi nạp tiền từ ngân hàng vào ví, "
        "tài khoản ngân hàng đã trừ tiền nhưng ví vẫn báo 0 đồng. "
        "Mã giao dịch TXN_TOPUP_001"
    )


def _topup_complaint_with_amount():
    return (
        "Tôi nạp 500.000đ từ ngân hàng vào ví, "
        "tài khoản ngân hàng đã trừ tiền nhưng ví vẫn báo 0 đồng. "
        "Mã giao dịch TXN_TOPUP_001"
    )


def _topup_evidence():
    return EvidenceBundle(
        transaction=Transaction.model_validate({
            "transaction_id": "TXN_TOPUP_001",
            "user_id": "U001",
            "service_type": "wallet_topup",
            "amount": 500_000,
            "status": "pending",
        }),
        reconciliation_status=ReconciliationStatus.model_validate({
            "transaction_id": "TXN_TOPUP_001",
            "bank_status": "success",
            "bank_amount": 500_000,
            "money_received_in_master_wallet": True,
            "mismatch_type": "bank_success_wallet_pending",
        }),
    )


# ═══════════════════════════════════════════════════════════════
# 1. CLAIM EXTRACTION — "ví vẫn 0đ" does NOT create amount claim
# ═══════════════════════════════════════════════════════════════


def test_vi_hien_thi_0d_is_wallet_balance_not_amount():
    """'ví hiển thị 0đ' → wallet_balance_claim, NOT transaction_amount."""
    claims = extract_claims("ví hiển thị 0đ")
    types = [c.claim_type for c in claims]
    assert ClaimType.WALLET_BALANCE in types
    assert ClaimType.TRANSACTION_AMOUNT not in types


def test_vi_van_bao_0_dong_is_wallet_balance():
    """'ví vẫn báo 0 đồng' → wallet_balance_claim."""
    claims = extract_claims("ví vẫn báo 0 đồng")
    types = [c.claim_type for c in claims]
    assert ClaimType.WALLET_BALANCE in types
    assert ClaimType.TRANSACTION_AMOUNT not in types
    wc = next(c for c in claims if c.claim_type == ClaimType.WALLET_BALANCE)
    assert wc.normalized_value == 0


def test_so_du_vi_con_0d_is_wallet_balance():
    """'số dư ví còn 0đ' → wallet_balance_claim."""
    claims = extract_claims("số dư ví còn 0đ")
    types = [c.claim_type for c in claims]
    assert ClaimType.WALLET_BALANCE in types
    assert ClaimType.TRANSACTION_AMOUNT not in types


def test_nap_500000_is_transaction_amount():
    """'Tôi nạp 500.000đ' → transaction_amount_claim = 500000."""
    claims = extract_claims("Tôi nạp 500.000đ")
    types = [c.claim_type for c in claims]
    assert ClaimType.TRANSACTION_AMOUNT in types
    ac = next(c for c in claims if c.claim_type == ClaimType.TRANSACTION_AMOUNT)
    assert ac.normalized_value == 500_000


def test_txn_id_numbers_not_amount():
    """Numbers inside TXN_TOPUP_001 must NOT become transaction_amount."""
    claims = extract_claims("Mã giao dịch TXN_TOPUP_001")
    types = [c.claim_type for c in claims]
    assert ClaimType.TRANSACTION_ID in types
    assert ClaimType.TRANSACTION_AMOUNT not in types


def test_topup_complaint_no_explicit_amount_claims():
    """Full topup complaint without explicit amount → no TRANSACTION_AMOUNT claim."""
    claims = extract_claims(_topup_complaint())
    types = {c.claim_type for c in claims}

    assert ClaimType.TRANSACTION_ID in types
    assert ClaimType.WALLET_BALANCE in types
    assert ClaimType.PAYMENT_STATUS in types
    # Customer did NOT say "nạp 500.000đ" — so no transaction_amount_claim
    assert ClaimType.TRANSACTION_AMOUNT not in types


def test_topup_complaint_with_explicit_amount_claims():
    """Topup complaint WITH explicit amount → has TRANSACTION_AMOUNT claim."""
    claims = extract_claims(_topup_complaint_with_amount())
    types = {c.claim_type for c in claims}

    assert ClaimType.TRANSACTION_ID in types
    assert ClaimType.WALLET_BALANCE in types
    assert ClaimType.TRANSACTION_AMOUNT in types
    ac = next(c for c in claims if c.claim_type == ClaimType.TRANSACTION_AMOUNT)
    assert ac.normalized_value == 500_000


# ═══════════════════════════════════════════════════════════════
# 2. amount_claimed FIELD — not set from wallet-balance context
# ═══════════════════════════════════════════════════════════════


def test_amount_claimed_not_set_from_wallet_context():
    """When only wallet balance mentioned (not transaction amount), amount_claimed=None."""
    extracted = mock_extract(_topup_complaint())
    assert extracted.amount_claimed is None, (
        f"amount_claimed should be None but got {extracted.amount_claimed}. "
        f"'ví vẫn báo 0 đồng' describes wallet balance, not transaction amount."
    )


def test_amount_claimed_set_from_explicit_topup():
    """When 'nạp 500.000đ' is explicit, amount_claimed=500000."""
    extracted = mock_extract(_topup_complaint_with_amount())
    assert extracted.amount_claimed == 500_000


# ═══════════════════════════════════════════════════════════════
# 3. CLAIM VERIFICATION — system_only for missing customer amount
# ═══════════════════════════════════════════════════════════════


def test_topup_no_amount_creates_system_only_claim():
    """Topup without explicit amount → system_only claim for transaction_amount."""
    extracted = mock_extract(_topup_complaint())
    evidence = _topup_evidence()

    result = verify_all_claims(extracted, evidence)

    # Find the transaction_amount claim
    amount_claims = [
        c for c in result.claims
        if c.claim_type == ClaimType.TRANSACTION_AMOUNT
    ]
    assert len(amount_claims) == 1, (
        f"Expected exactly 1 transaction_amount claim, got {len(amount_claims)}"
    )

    ac = amount_claims[0]
    assert ac.verification_status == VerificationStatus.SYSTEM_ONLY
    assert ac.customer_claimed_value is None
    assert ac.trusted_system_value == 500_000
    assert ac.trusted_source == "transaction.amount"
    assert "Khách không cung cấp" in ac.explanation
    assert "500,000" in ac.explanation


def test_topup_no_amount_does_not_create_mismatch():
    """Topup without explicit amount must NOT produce a mismatch."""
    extracted = mock_extract(_topup_complaint())
    evidence = _topup_evidence()

    result = verify_all_claims(extracted, evidence)

    # No mismatch on transaction_amount
    assert ClaimType.TRANSACTION_AMOUNT not in result.mismatched_claims
    # The overall has_customer_detail_mismatch should NOT be triggered by system_only
    # (it could still be triggered by other claims, but not by the amount)


def test_topup_with_correct_amount_is_matched():
    """Topup with correct amount → matched."""
    extracted = mock_extract(_topup_complaint_with_amount())
    evidence = _topup_evidence()

    result = verify_all_claims(extracted, evidence)

    amount_claims = [
        c for c in result.claims
        if c.claim_type == ClaimType.TRANSACTION_AMOUNT
    ]
    assert len(amount_claims) == 1
    assert amount_claims[0].verification_status == VerificationStatus.MATCHED


def test_wallet_balance_claim_separate_from_amount():
    """Wallet balance claim is a separate row from transaction amount."""
    extracted = mock_extract(_topup_complaint())
    evidence = _topup_evidence()

    result = verify_all_claims(extracted, evidence)

    # There should be BOTH a wallet_balance claim AND a transaction_amount claim
    claim_types = {c.claim_type for c in result.claims}
    assert ClaimType.WALLET_BALANCE in claim_types
    assert ClaimType.TRANSACTION_AMOUNT in claim_types

    # wallet_balance claim has customer value = 0
    wb = next(c for c in result.claims if c.claim_type == ClaimType.WALLET_BALANCE)
    assert wb.customer_claimed_value == 0

    # transaction_amount claim has customer value = None (system_only)
    ta = next(c for c in result.claims if c.claim_type == ClaimType.TRANSACTION_AMOUNT)
    assert ta.customer_claimed_value is None
    assert ta.trusted_system_value == 500_000


# ═══════════════════════════════════════════════════════════════
# 4. ACTION AMOUNT — always from system evidence
# ═══════════════════════════════════════════════════════════════


def test_action_amount_uses_system_evidence():
    """Action amount always comes from transaction.amount, not wallet_balance_claim."""
    extracted = mock_extract(_topup_complaint())
    evidence = _topup_evidence()

    result = verify_all_claims(extracted, evidence)

    # trusted_data_used_for_action must have action_amount from system
    assert result.trusted_data_used_for_action["action_amount"] == 500_000
    assert result.trusted_data_used_for_action["action_amount_source"] == "transaction.amount"


def test_wallet_balance_claim_never_used_as_action_amount():
    """Wallet balance value (0đ) is NOT used as action amount."""
    extracted = mock_extract(_topup_complaint())
    evidence = _topup_evidence()

    result = verify_all_claims(extracted, evidence)
    assert result.trusted_data_used_for_action["action_amount"] != 0


# ═══════════════════════════════════════════════════════════════
# 5. STAFF EXPLANATION
# ═══════════════════════════════════════════════════════════════


def test_staff_explanation_not_empty():
    """Staff explanation should be generated."""
    extracted = mock_extract(_topup_complaint())
    evidence = _topup_evidence()

    result = verify_all_claims(extracted, evidence)
    assert result.staff_explanation
    assert len(result.staff_explanation) > 10


# ═══════════════════════════════════════════════════════════════
# 6. SYSTEM_ONLY STATUS
# ═══════════════════════════════════════════════════════════════


def test_system_only_not_counted_as_mismatch():
    """system_only claims should NOT be in mismatched_claims list."""
    extracted = mock_extract(_topup_complaint())
    evidence = _topup_evidence()

    result = verify_all_claims(extracted, evidence)

    # transaction_amount should NOT be in mismatched_claims
    assert ClaimType.TRANSACTION_AMOUNT not in result.mismatched_claims


def test_summary_includes_system_only_count():
    """Summary text should mention system_only claims."""
    extracted = mock_extract(_topup_complaint())
    evidence = _topup_evidence()

    result = verify_all_claims(extracted, evidence)
    assert "hệ thống" in result.summary.lower()
