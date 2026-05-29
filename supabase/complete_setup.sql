-- ============================================================
-- COMPLETE MIGRATION + SEED — Copy-paste này vào Supabase SQL Editor
-- Chạy 1 lần duy nhất
-- ============================================================

-- ═══════════════════════════════════════════
-- STEP 1: CREATE TABLES
-- ═══════════════════════════════════════════

CREATE TABLE IF NOT EXISTS cases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id text UNIQUE NOT NULL,
    user_id text,
    transaction_id text,
    raw_complaint text NOT NULL,
    service_type text,
    issue_type text,
    selected_workflow text,
    recommended_action text,
    risk_level text,
    approval_required boolean DEFAULT false,
    status text NOT NULL DEFAULT 'new',
    missing_fields jsonb DEFAULT '[]'::jsonb,
    result_snapshot jsonb DEFAULT '{}'::jsonb,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS transactions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id text UNIQUE NOT NULL,
    user_id text NOT NULL,
    service_type text NOT NULL,
    amount integer NOT NULL,
    status text NOT NULL,
    provider_ref_id text,
    order_id text,
    bill_code text,
    customer_code text,
    created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wallet_ledger_entries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_id text UNIQUE NOT NULL,
    transaction_id text NOT NULL,
    user_id text NOT NULL,
    entry_type text NOT NULL,
    amount integer NOT NULL,
    balance_after integer,
    reason text,
    status text NOT NULL DEFAULT 'active',
    has_user_debit boolean DEFAULT false,
    debit_amount integer DEFAULT 0,
    has_credit_refund boolean DEFAULT false,
    credit_refund_amount integer DEFAULT 0,
    net_amount integer DEFAULT 0,
    created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS train_provider_statuses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_ref_id text UNIQUE NOT NULL,
    transaction_id text NOT NULL,
    status text NOT NULL,
    ticket_code text,
    order_id text,
    message text,
    updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS utility_provider_statuses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_ref_id text UNIQUE NOT NULL,
    transaction_id text NOT NULL,
    service_type text NOT NULL,
    status text NOT NULL,
    bill_code text,
    customer_code text,
    confirmation_code text,
    amount integer,
    message text,
    updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS refunds (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    refund_id text UNIQUE,
    transaction_id text NOT NULL,
    status text NOT NULL,
    amount integer,
    idempotency_key text UNIQUE,
    reason text,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reconciliation_cases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reconciliation_id text UNIQUE,
    transaction_id text NOT NULL,
    status text NOT NULL,
    mismatch_type text,
    details jsonb DEFAULT '{}'::jsonb,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS approval_packets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    approval_id text UNIQUE NOT NULL,
    case_id text NOT NULL,
    action_type text NOT NULL,
    transaction_id text,
    amount integer,
    risk_level text,
    status text NOT NULL DEFAULT 'pending',
    evidence jsonb DEFAULT '[]'::jsonb,
    idempotency_key text,
    reviewer_id text,
    decision_reason text,
    created_at timestamptz DEFAULT now(),
    decided_at timestamptz
);

CREATE TABLE IF NOT EXISTS audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id text UNIQUE NOT NULL,
    case_id text NOT NULL,
    actor text NOT NULL,
    event_type text NOT NULL,
    previous_status text,
    new_status text,
    details jsonb DEFAULT '{}'::jsonb,
    correlation_id text,
    created_at timestamptz DEFAULT now()
);

-- INDEXES
CREATE INDEX IF NOT EXISTS idx_cases_case_id ON cases(case_id);
CREATE INDEX IF NOT EXISTS idx_transactions_transaction_id ON transactions(transaction_id);
CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_wallet_ledger_transaction_id ON wallet_ledger_entries(transaction_id);
CREATE INDEX IF NOT EXISTS idx_train_provider_transaction_id ON train_provider_statuses(transaction_id);
CREATE INDEX IF NOT EXISTS idx_utility_provider_transaction_id ON utility_provider_statuses(transaction_id);
CREATE INDEX IF NOT EXISTS idx_refunds_transaction_id ON refunds(transaction_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_case_id ON audit_events(case_id);

-- ═══════════════════════════════════════════
-- STEP 2: SEED DATA
-- ═══════════════════════════════════════════

INSERT INTO transactions (transaction_id, user_id, service_type, amount, status, order_id, bill_code, customer_code, provider_ref_id, created_at)
VALUES
  ('TXN_TRAIN_001', 'U001', 'train_ticket', 450000, 'completed', 'ORDER_TRAIN_001', NULL, NULL, 'TRAIN_REF_001', '2026-05-27T10:05:00Z'),
  ('TXN_TRAIN_002', 'U002', 'train_ticket', 350000, 'completed', 'ORDER_TRAIN_002', NULL, NULL, 'TRAIN_REF_002', '2026-05-27T09:30:00Z'),
  ('TXN_TRAIN_003', 'U002', 'train_ticket', 520000, 'completed', 'ORDER_TRAIN_003', NULL, NULL, 'TRAIN_REF_003', '2026-05-27T08:00:00Z'),
  ('TXN_BILL_001',  'U003', 'electric_bill', 720000, 'completed', NULL, 'EVN123456', 'KH998877', 'EVN_REF_001', '2026-05-27T11:00:00Z'),
  ('TXN_BILL_002',  'U004', 'electric_bill', 480000, 'completed', NULL, 'EVN789012', 'KH112233', 'EVN_REF_002', '2026-05-27T12:00:00Z'),
  ('TXN_BILL_003',  'U005', 'water_bill',    310000, 'completed', NULL, 'WATER_001', 'KH556677', 'WATER_REF_001', '2026-05-27T13:00:00Z'),
  ('TXN_CONFLICT_001', 'U006', 'train_ticket', 400000, 'pending', 'ORDER_CONFLICT_001', NULL, NULL, 'TRAIN_REF_CONFLICT_001', '2026-05-27T14:00:00Z'),
  ('TXN_REFUND_001',   'U007', 'train_ticket', 450000, 'completed', 'ORDER_REFUND_001', NULL, NULL, 'TRAIN_REF_REFUND_001', '2026-05-26T10:00:00Z')
ON CONFLICT (transaction_id) DO UPDATE SET
  user_id = EXCLUDED.user_id, service_type = EXCLUDED.service_type, amount = EXCLUDED.amount,
  status = EXCLUDED.status, order_id = EXCLUDED.order_id, bill_code = EXCLUDED.bill_code,
  customer_code = EXCLUDED.customer_code, provider_ref_id = EXCLUDED.provider_ref_id;

INSERT INTO wallet_ledger_entries (entry_id, transaction_id, user_id, entry_type, amount, balance_after, reason, status, has_user_debit, debit_amount, has_credit_refund, credit_refund_amount, net_amount, created_at)
VALUES
  ('WLE_TRAIN_001_D', 'TXN_TRAIN_001', 'U001', 'debit', 450000, 550000, 'train_ticket_purchase', 'debited', true, 450000, false, 0, 450000, '2026-05-27T10:05:00Z'),
  ('WLE_TRAIN_002_D', 'TXN_TRAIN_002', 'U002', 'debit', 350000, 200000, 'train_ticket_purchase', 'debited', true, 350000, false, 0, 350000, '2026-05-27T09:30:00Z'),
  ('WLE_TRAIN_003_D', 'TXN_TRAIN_003', 'U002', 'debit', 520000, 480000, 'train_ticket_purchase', 'debited', true, 520000, false, 0, 520000, '2026-05-27T08:00:00Z'),
  ('WLE_BILL_001_D',  'TXN_BILL_001',  'U003', 'debit', 720000, 280000, 'electric_bill_payment', 'debited', true, 720000, false, 0, 720000, '2026-05-27T11:00:00Z'),
  ('WLE_BILL_002_D',  'TXN_BILL_002',  'U004', 'debit', 480000, 520000, 'electric_bill_payment', 'debited', true, 480000, false, 0, 480000, '2026-05-27T12:00:00Z'),
  ('WLE_BILL_003_D',  'TXN_BILL_003',  'U005', 'debit', 310000, 690000, 'water_bill_payment',    'debited', true, 310000, false, 0, 310000, '2026-05-27T13:00:00Z'),
  ('WLE_CONFLICT_D',  'TXN_CONFLICT_001', 'U006', 'debit', 400000, 600000, 'train_ticket_purchase', 'debited', true, 400000, false, 0, 400000, '2026-05-27T14:00:00Z'),
  ('WLE_REFUND_001_D', 'TXN_REFUND_001', 'U007', 'debit', 450000, 550000, 'train_ticket_purchase', 'refunded', true, 450000, true, 450000, 0, '2026-05-26T10:00:00Z'),
  ('WLE_REFUND_001_C', 'TXN_REFUND_001', 'U007', 'credit', 450000, 1000000, 'refund', 'refunded', true, 450000, true, 450000, 0, '2026-05-26T12:00:00Z')
ON CONFLICT (entry_id) DO UPDATE SET
  transaction_id = EXCLUDED.transaction_id, user_id = EXCLUDED.user_id,
  entry_type = EXCLUDED.entry_type, amount = EXCLUDED.amount;

INSERT INTO train_provider_statuses (provider_ref_id, transaction_id, status, ticket_code, order_id, updated_at)
VALUES
  ('TRAIN_REF_001', 'TXN_TRAIN_001', 'ticket_not_issued', NULL, 'ORDER_TRAIN_001', now()),
  ('TRAIN_REF_002', 'TXN_TRAIN_002', 'ticket_issued', 'PNR_ABC123', 'ORDER_TRAIN_002', now()),
  ('TRAIN_REF_003', 'TXN_TRAIN_003', 'provider_no_record', NULL, 'ORDER_TRAIN_003', now()),
  ('TRAIN_REF_CONFLICT_001', 'TXN_CONFLICT_001', 'ticket_not_issued', NULL, 'ORDER_CONFLICT_001', now()),
  ('TRAIN_REF_REFUND_001', 'TXN_REFUND_001', 'ticket_not_issued', NULL, 'ORDER_REFUND_001', now())
ON CONFLICT (provider_ref_id) DO UPDATE SET
  transaction_id = EXCLUDED.transaction_id, status = EXCLUDED.status,
  ticket_code = EXCLUDED.ticket_code, order_id = EXCLUDED.order_id;

INSERT INTO utility_provider_statuses (provider_ref_id, transaction_id, service_type, status, bill_code, customer_code, amount, updated_at)
VALUES
  ('EVN_REF_001',   'TXN_BILL_001', 'electric_bill', 'confirmed',     'EVN123456', 'KH998877', 720000, now()),
  ('EVN_REF_002',   'TXN_BILL_002', 'electric_bill', 'not_confirmed', 'EVN789012', 'KH112233', 480000, now()),
  ('WATER_REF_001', 'TXN_BILL_003', 'water_bill',    'failed',        'WATER_001', 'KH556677', 310000, now())
ON CONFLICT (provider_ref_id) DO UPDATE SET
  transaction_id = EXCLUDED.transaction_id, service_type = EXCLUDED.service_type,
  status = EXCLUDED.status, bill_code = EXCLUDED.bill_code,
  customer_code = EXCLUDED.customer_code, amount = EXCLUDED.amount;

INSERT INTO refunds (transaction_id, status, amount, reason, created_at, updated_at)
VALUES
  ('TXN_TRAIN_001',    'not_requested', NULL, NULL, now(), now()),
  ('TXN_TRAIN_002',    'not_requested', NULL, NULL, now(), now()),
  ('TXN_TRAIN_003',    'not_requested', NULL, NULL, now(), now()),
  ('TXN_BILL_001',     'not_requested', NULL, NULL, now(), now()),
  ('TXN_BILL_002',     'not_requested', NULL, NULL, now(), now()),
  ('TXN_BILL_003',     'not_requested', NULL, NULL, now(), now()),
  ('TXN_CONFLICT_001', 'not_requested', NULL, NULL, now(), now());

INSERT INTO refunds (refund_id, transaction_id, status, amount, reason, created_at, updated_at)
VALUES
  ('REFUND_001', 'TXN_REFUND_001', 'executed', 450000, 'refund_approved', '2026-05-26T11:00:00Z', '2026-05-26T12:00:00Z')
ON CONFLICT (refund_id) DO UPDATE SET
  transaction_id = EXCLUDED.transaction_id, status = EXCLUDED.status, amount = EXCLUDED.amount;

INSERT INTO reconciliation_cases (reconciliation_id, transaction_id, status, mismatch_type, details, created_at)
VALUES
  ('RECON_BILL_002', 'TXN_BILL_002', 'wallet_provider_mismatch', 'wallet_debited_provider_not_confirmed', '{}', '2026-05-27T12:30:00Z')
ON CONFLICT (reconciliation_id) DO UPDATE SET
  transaction_id = EXCLUDED.transaction_id, status = EXCLUDED.status, mismatch_type = EXCLUDED.mismatch_type;

-- ═══════════════════════════════════════════
-- DONE! Verify:
-- SELECT count(*) FROM transactions;         -- should be 8
-- SELECT count(*) FROM wallet_ledger_entries; -- should be 9
-- SELECT count(*) FROM refunds;               -- should be 8
-- ═══════════════════════════════════════════
