-- ============================================================
-- Fintech Agent — Merchant Settlement Tables Migration
-- Run this in Supabase SQL Editor
-- ============================================================

-- 1. merchants
CREATE TABLE IF NOT EXISTS merchants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id text UNIQUE NOT NULL,
    merchant_name text,
    tax_code text,
    contact_email text,
    phone text,
    status text DEFAULT 'active',
    settlement_cycle text DEFAULT 'D+1',
    bank_account_id text,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_merchants_merchant_id ON merchants(merchant_id);
CREATE INDEX IF NOT EXISTS idx_merchants_phone ON merchants(phone);
CREATE INDEX IF NOT EXISTS idx_merchants_email ON merchants(contact_email);
CREATE INDEX IF NOT EXISTS idx_merchants_tax_code ON merchants(tax_code);

-- 2. merchant_bank_accounts
CREATE TABLE IF NOT EXISTS merchant_bank_accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bank_account_id text UNIQUE NOT NULL,
    merchant_id text NOT NULL,
    bank_code text,
    bank_name text,
    account_number text,
    account_holder_name text,
    branch_name text,
    verification_status text DEFAULT 'verified',
    is_active boolean DEFAULT true,
    failure_reason text,
    last_verified_at timestamptz,
    updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_merchant_bank_accounts_merchant_id ON merchant_bank_accounts(merchant_id);

-- 3. settlement_batches
CREATE TABLE IF NOT EXISTS settlement_batches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id text UNIQUE NOT NULL,
    settlement_date text NOT NULL,
    cycle text DEFAULT 'D+1',
    status text NOT NULL DEFAULT 'pending',
    total_merchants integer DEFAULT 0,
    total_amount integer DEFAULT 0,
    started_at timestamptz,
    finished_at timestamptz,
    failure_reason text,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_settlement_batches_batch_id ON settlement_batches(batch_id);
CREATE INDEX IF NOT EXISTS idx_settlement_batches_date_cycle ON settlement_batches(settlement_date, cycle);

-- 4. merchant_settlement_ledgers
CREATE TABLE IF NOT EXISTS merchant_settlement_ledgers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ledger_id text UNIQUE NOT NULL,
    merchant_id text NOT NULL,
    settlement_date text NOT NULL,
    due_date text,
    gross_amount integer DEFAULT 0,
    fee_amount integer DEFAULT 0,
    refund_amount integer DEFAULT 0,
    chargeback_amount integer DEFAULT 0,
    net_settlement_amount integer DEFAULT 0,
    currency text DEFAULT 'VND',
    status text DEFAULT 'pending',
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_merchant_settlement_ledgers_merchant_id ON merchant_settlement_ledgers(merchant_id);
CREATE INDEX IF NOT EXISTS idx_merchant_settlement_ledgers_date ON merchant_settlement_ledgers(merchant_id, settlement_date);

-- 5. merchant_payouts
CREATE TABLE IF NOT EXISTS merchant_payouts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    payout_id text UNIQUE NOT NULL,
    batch_id text,
    merchant_id text NOT NULL,
    settlement_date text,
    bank_account_id text,
    amount integer DEFAULT 0,
    currency text DEFAULT 'VND',
    status text NOT NULL DEFAULT 'pending',
    bank_transfer_ref text,
    failure_reason text,
    scheduled_date text,
    executed_at timestamptz,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_merchant_payouts_merchant_id ON merchant_payouts(merchant_id);
CREATE INDEX IF NOT EXISTS idx_merchant_payouts_payout_id ON merchant_payouts(payout_id);
CREATE INDEX IF NOT EXISTS idx_merchant_payouts_batch_id ON merchant_payouts(batch_id);

-- 6. bank_transfer_receipts
CREATE TABLE IF NOT EXISTS bank_transfer_receipts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    receipt_id text UNIQUE NOT NULL,
    payout_id text,
    bank_transfer_ref text,
    bank_status text,
    unc_number text,
    receipt_url text,
    sent_to_merchant boolean DEFAULT false,
    sent_at timestamptz,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bank_transfer_receipts_payout_id ON bank_transfer_receipts(payout_id);
CREATE INDEX IF NOT EXISTS idx_bank_transfer_receipts_ref ON bank_transfer_receipts(bank_transfer_ref);

-- NOTE: merchant_settlement_expected_tests table is NOT created here.
-- It is for human/evaluation use only and must NOT be exposed to the agent.
