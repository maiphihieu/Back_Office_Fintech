-- ============================================================
-- Fintech Agent — Initial Schema Migration
-- Run this in Supabase SQL Editor
-- ============================================================

-- 1. cases
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

CREATE INDEX IF NOT EXISTS idx_cases_case_id ON cases(case_id);

-- 2. transactions
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

CREATE INDEX IF NOT EXISTS idx_transactions_transaction_id ON transactions(transaction_id);
CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id);

-- 3. wallet_ledger_entries
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

CREATE INDEX IF NOT EXISTS idx_wallet_ledger_transaction_id ON wallet_ledger_entries(transaction_id);

-- 4. train_provider_statuses
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

CREATE INDEX IF NOT EXISTS idx_train_provider_transaction_id ON train_provider_statuses(transaction_id);

-- 5. utility_provider_statuses
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

CREATE INDEX IF NOT EXISTS idx_utility_provider_transaction_id ON utility_provider_statuses(transaction_id);

-- 6. refunds
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

CREATE INDEX IF NOT EXISTS idx_refunds_transaction_id ON refunds(transaction_id);

-- 7. reconciliation_cases
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

-- 8. approval_packets
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

-- 9. audit_events
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

CREATE INDEX IF NOT EXISTS idx_audit_events_case_id ON audit_events(case_id);
