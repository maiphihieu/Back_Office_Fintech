-- ============================================================
-- Migration 002: Fraud & Account Lock tables
-- Use case 2: "Account locked by Fraud Detection"
-- ============================================================

create table if not exists accounts (
  user_id text primary key,
  wallet_id text,
  account_status text not null,
  withdrawal_enabled boolean not null default false,
  lock_reason text,
  current_balance numeric default 0,
  locked_at timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists fraud_cases (
  fraud_case_id text primary key,
  user_id text not null references accounts(user_id),
  risk_score int,
  risk_level text,
  fraud_status text,
  trigger_reason text,
  details jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
