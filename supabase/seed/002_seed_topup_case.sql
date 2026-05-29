-- ============================================================
-- SEED: Wallet Topup Case (TXN_TOPUP_001)
-- Run this in Supabase SQL Editor after initial setup
-- ============================================================

-- Transaction: pending wallet topup
INSERT INTO transactions (
    transaction_id, user_id, service_type, amount, status,
    provider_ref_id, created_at
) VALUES (
    'TXN_TOPUP_001', 'U_TOPUP_001', 'wallet_topup', 500000, 'pending',
    'BANK_REF_001', '2026-05-28T08:00:00Z'
) ON CONFLICT (transaction_id) DO NOTHING;

-- Reconciliation: bank success, money received in master wallet
-- Note: reconciliation_cases.transaction_id is NOT UNIQUE, so no ON CONFLICT
INSERT INTO reconciliation_cases (
    transaction_id, status, mismatch_type, details, created_at
)
SELECT
    'TXN_TOPUP_001',
    'matched',
    'bank_success_wallet_pending',
    '{
        "bank_status": "success",
        "bank_amount": 500000,
        "money_received_in_master_wallet": true,
        "bank_ref_id": "BANK_REF_001",
        "note": "Bank confirmed success, money received in master wallet"
    }'::jsonb,
    '2026-05-28T08:05:00Z'
WHERE NOT EXISTS (
    SELECT 1 FROM reconciliation_cases WHERE transaction_id = 'TXN_TOPUP_001'
);
