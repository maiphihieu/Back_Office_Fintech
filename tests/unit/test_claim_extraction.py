"""Tests for generic claim extraction — semantic number classification.

Tests that the extract_claims() function correctly classifies numbers by
their semantic role in the complaint text:
- Wallet balance numbers → wallet_balance_claim
- Payment/top-up amounts → transaction_amount_claim
- Transaction ID numbers → transaction_id_claim (not amount!)
- Unclear numbers → not classified as transaction_amount
"""

from fintech_agent.llm.mock_extractor import extract_claims
from fintech_agent.schemas.claim_verification import ClaimType


# ─── Wallet balance is NOT transaction amount ─────────────────


def test_vi_van_0d_is_wallet_balance():
    """'ví vẫn 0đ' → wallet_balance_claim, value=0."""
    claims = extract_claims("ví vẫn 0đ")
    types = [c.claim_type for c in claims]
    assert ClaimType.WALLET_BALANCE in types
    wc = next(c for c in claims if c.claim_type == ClaimType.WALLET_BALANCE)
    assert wc.normalized_value == 0


def test_vi_bao_0d_is_wallet_balance():
    """'ví báo 0đ' → wallet_balance_claim, value=0."""
    claims = extract_claims("ví báo 0đ")
    types = [c.claim_type for c in claims]
    assert ClaimType.WALLET_BALANCE in types


def test_so_du_is_wallet_balance():
    """'số dư 500.000đ' → wallet_balance_claim."""
    claims = extract_claims("số dư 500.000đ")
    types = [c.claim_type for c in claims]
    assert ClaimType.WALLET_BALANCE in types
    wc = next(c for c in claims if c.claim_type == ClaimType.WALLET_BALANCE)
    assert wc.normalized_value == 500000


# ─── Transaction amount ──────────────────────────────────────


def test_nap_amount_is_transaction_amount():
    """'nạp 500.000đ' → transaction_amount_claim."""
    claims = extract_claims("nạp 500.000đ")
    types = [c.claim_type for c in claims]
    assert ClaimType.TRANSACTION_AMOUNT in types
    ac = next(c for c in claims if c.claim_type == ClaimType.TRANSACTION_AMOUNT)
    assert ac.normalized_value == 500000


def test_thanh_toan_amount_is_transaction_amount():
    """'thanh toán 450.000 VND' → transaction_amount_claim."""
    claims = extract_claims("thanh toán 450.000 VND")
    types = [c.claim_type for c in claims]
    assert ClaimType.TRANSACTION_AMOUNT in types
    ac = next(c for c in claims if c.claim_type == ClaimType.TRANSACTION_AMOUNT)
    assert ac.normalized_value == 450000


def test_tru_amount_is_transaction_amount():
    """'trừ 200.000đ' → transaction_amount_claim."""
    claims = extract_claims("ngân hàng trừ 200.000đ")
    types = [c.claim_type for c in claims]
    assert ClaimType.TRANSACTION_AMOUNT in types


# ─── Transaction ID is not amount ────────────────────────────


def test_txn_id_number_is_not_amount():
    """'TXN_TOPUP_001' → transaction_id_claim, NOT transaction_amount."""
    claims = extract_claims("Mã giao dịch TXN_TOPUP_001")
    types = [c.claim_type for c in claims]
    assert ClaimType.TRANSACTION_ID in types
    # "001" in TXN_TOPUP_001 should NOT produce a transaction_amount
    assert ClaimType.TRANSACTION_AMOUNT not in types


def test_txn_id_extracted_correctly():
    """Transaction ID should be the full pattern."""
    claims = extract_claims("TXN_TRAIN_12345 bị lỗi")
    txn = next(c for c in claims if c.claim_type == ClaimType.TRANSACTION_ID)
    assert txn.customer_claimed_value == "TXN_TRAIN_12345"
    assert txn.unit == "identifier"


# ─── Payment status ──────────────────────────────────────────


def test_bank_deducted_is_payment_status():
    """'ngân hàng đã trừ tiền' → payment_status_claim."""
    claims = extract_claims("ngân hàng đã trừ tiền")
    types = [c.claim_type for c in claims]
    assert ClaimType.PAYMENT_STATUS in types
    ps = next(c for c in claims if c.claim_type == ClaimType.PAYMENT_STATUS)
    assert ps.customer_claimed_value == "bank_deducted"


def test_da_thanh_toan_is_payment_status():
    """'đã thanh toán thành công' → payment_status_claim."""
    claims = extract_claims("đã thanh toán thành công")
    types = [c.claim_type for c in claims]
    assert ClaimType.PAYMENT_STATUS in types


# ─── Service delivery ────────────────────────────────────────


def test_chua_nhan_ve_is_service_delivery():
    """'chưa nhận được vé' → service_delivery_claim."""
    claims = extract_claims("chưa nhận được vé")
    types = [c.claim_type for c in claims]
    assert ClaimType.SERVICE_DELIVERY in types
    sd = next(c for c in claims if c.claim_type == ClaimType.SERVICE_DELIVERY)
    assert sd.customer_claimed_value == "ticket_not_received"


# ─── Account status ──────────────────────────────────────────


def test_account_locked_claim():
    """'tài khoản bị khóa' → account_status_claim."""
    claims = extract_claims("tài khoản bị khóa")
    types = [c.claim_type for c in claims]
    assert ClaimType.ACCOUNT_STATUS in types
    ac = next(c for c in claims if c.claim_type == ClaimType.ACCOUNT_STATUS)
    assert ac.customer_claimed_value == "account_locked"


# ─── Refund status ───────────────────────────────────────────


def test_refund_not_received_claim():
    """'chưa nhận hoàn tiền' → refund_status_claim."""
    claims = extract_claims("chưa nhận hoàn tiền")
    types = [c.claim_type for c in claims]
    assert ClaimType.REFUND_STATUS in types
    rc = next(c for c in claims if c.claim_type == ClaimType.REFUND_STATUS)
    assert rc.customer_claimed_value == "refund_not_received"


# ─── Composite complaints ────────────────────────────────────


def test_realistic_topup_complaint():
    """Full topup complaint extracts multiple claim types correctly."""
    claims = extract_claims(
        "Tôi nạp tiền, ngân hàng đã trừ tiền nhưng ví vẫn 0đ. "
        "Mã TXN_TOPUP_001."
    )
    types = {c.claim_type for c in claims}

    assert ClaimType.TRANSACTION_ID in types
    assert ClaimType.WALLET_BALANCE in types
    assert ClaimType.PAYMENT_STATUS in types


def test_train_ticket_complaint():
    """Train ticket complaint should extract transaction_id and service delivery."""
    claims = extract_claims(
        "Tôi đã thanh toán 350.000 VND cho vé tàu nhưng chưa nhận được vé. "
        "Mã giao dịch TXN_TRAIN_001."
    )
    types = {c.claim_type for c in claims}

    assert ClaimType.TRANSACTION_ID in types
    assert ClaimType.TRANSACTION_AMOUNT in types
    assert ClaimType.SERVICE_DELIVERY in types

    amount_claim = next(c for c in claims if c.claim_type == ClaimType.TRANSACTION_AMOUNT)
    assert amount_claim.normalized_value == 350000


def test_no_claims_from_empty_text():
    """Empty complaint should produce no claims."""
    claims = extract_claims("")
    assert len(claims) == 0


def test_no_claims_from_unrelated_text():
    """Unrelated text should produce no claims."""
    claims = extract_claims("Trời hôm nay đẹp quá.")
    assert len(claims) == 0
