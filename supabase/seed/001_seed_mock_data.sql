-- ============================================================
-- Fintech Agent — Seed Mock Data
-- Idempotent: uses INSERT ... ON CONFLICT DO UPDATE
-- Run this AFTER 001_initial_schema.sql
-- ============================================================

-- ═══════════════════════════════════════════
-- TRANSACTIONS
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
  user_id = EXCLUDED.user_id,
  service_type = EXCLUDED.service_type,
  amount = EXCLUDED.amount,
  status = EXCLUDED.status,
  order_id = EXCLUDED.order_id,
  bill_code = EXCLUDED.bill_code,
  customer_code = EXCLUDED.customer_code,
  provider_ref_id = EXCLUDED.provider_ref_id;

-- ═══════════════════════════════════════════
-- WALLET LEDGER ENTRIES
-- Each transaction has 1+ entries. REFUND_001 has 2 (debit + credit).
-- Fields: has_user_debit, debit_amount etc are per-entry metadata for the Supabase repo
-- to aggregate into the WalletLedger Pydantic model.
-- ═══════════════════════════════════════════

INSERT INTO wallet_ledger_entries (entry_id, transaction_id, user_id, entry_type, amount, balance_after, reason, status, has_user_debit, debit_amount, has_credit_refund, credit_refund_amount, net_amount, created_at)
VALUES
  ('WLE_TRAIN_001_D', 'TXN_TRAIN_001', 'U001', 'debit', 450000, 550000, 'train_ticket_purchase', 'debited', true, 450000, false, 0, 450000, '2026-05-27T10:05:00Z'),
  ('WLE_TRAIN_002_D', 'TXN_TRAIN_002', 'U002', 'debit', 350000, 200000, 'train_ticket_purchase', 'debited', true, 350000, false, 0, 350000, '2026-05-27T09:30:00Z'),
  ('WLE_TRAIN_003_D', 'TXN_TRAIN_003', 'U002', 'debit', 520000, 480000, 'train_ticket_purchase', 'debited', true, 520000, false, 0, 520000, '2026-05-27T08:00:00Z'),
  ('WLE_BILL_001_D',  'TXN_BILL_001',  'U003', 'debit', 720000, 280000, 'electric_bill_payment', 'debited', true, 720000, false, 0, 720000, '2026-05-27T11:00:00Z'),
  ('WLE_BILL_002_D',  'TXN_BILL_002',  'U004', 'debit', 480000, 520000, 'electric_bill_payment', 'debited', true, 480000, false, 0, 480000, '2026-05-27T12:00:00Z'),
  ('WLE_BILL_003_D',  'TXN_BILL_003',  'U005', 'debit', 310000, 690000, 'water_bill_payment',    'debited', true, 310000, false, 0, 310000, '2026-05-27T13:00:00Z'),
  ('WLE_CONFLICT_D',  'TXN_CONFLICT_001', 'U006', 'debit', 400000, 600000, 'train_ticket_purchase', 'debited', true, 400000, false, 0, 400000, '2026-05-27T14:00:00Z'),
  -- REFUND_001: debit entry
  ('WLE_REFUND_001_D', 'TXN_REFUND_001', 'U007', 'debit', 450000, 550000, 'train_ticket_purchase', 'refunded', true, 450000, true, 450000, 0, '2026-05-26T10:00:00Z'),
  -- REFUND_001: credit (refund) entry
  ('WLE_REFUND_001_C', 'TXN_REFUND_001', 'U007', 'credit', 450000, 1000000, 'refund', 'refunded', true, 450000, true, 450000, 0, '2026-05-26T12:00:00Z')
ON CONFLICT (entry_id) DO UPDATE SET
  transaction_id = EXCLUDED.transaction_id,
  user_id = EXCLUDED.user_id,
  entry_type = EXCLUDED.entry_type,
  amount = EXCLUDED.amount,
  balance_after = EXCLUDED.balance_after,
  reason = EXCLUDED.reason,
  status = EXCLUDED.status,
  has_user_debit = EXCLUDED.has_user_debit,
  debit_amount = EXCLUDED.debit_amount,
  has_credit_refund = EXCLUDED.has_credit_refund,
  credit_refund_amount = EXCLUDED.credit_refund_amount,
  net_amount = EXCLUDED.net_amount;

-- ═══════════════════════════════════════════
-- TRAIN PROVIDER STATUSES
-- ═══════════════════════════════════════════

INSERT INTO train_provider_statuses (provider_ref_id, transaction_id, status, ticket_code, order_id, updated_at)
VALUES
  ('TRAIN_REF_001', 'TXN_TRAIN_001', 'ticket_not_issued', NULL, 'ORDER_TRAIN_001', now()),
  ('TRAIN_REF_002', 'TXN_TRAIN_002', 'ticket_issued', 'PNR_ABC123', 'ORDER_TRAIN_002', now()),
  ('TRAIN_REF_003', 'TXN_TRAIN_003', 'provider_no_record', NULL, 'ORDER_TRAIN_003', now()),
  ('TRAIN_REF_CONFLICT_001', 'TXN_CONFLICT_001', 'ticket_not_issued', NULL, 'ORDER_CONFLICT_001', now()),
  ('TRAIN_REF_REFUND_001', 'TXN_REFUND_001', 'ticket_not_issued', NULL, 'ORDER_REFUND_001', now())
ON CONFLICT (provider_ref_id) DO UPDATE SET
  transaction_id = EXCLUDED.transaction_id,
  status = EXCLUDED.status,
  ticket_code = EXCLUDED.ticket_code,
  order_id = EXCLUDED.order_id;

-- ═══════════════════════════════════════════
-- UTILITY PROVIDER STATUSES
-- ═══════════════════════════════════════════

INSERT INTO utility_provider_statuses (provider_ref_id, transaction_id, service_type, status, bill_code, customer_code, amount, updated_at)
VALUES
  ('EVN_REF_001',   'TXN_BILL_001', 'electric_bill', 'confirmed',     'EVN123456', 'KH998877', 720000, now()),
  ('EVN_REF_002',   'TXN_BILL_002', 'electric_bill', 'not_confirmed', 'EVN789012', 'KH112233', 480000, now()),
  ('WATER_REF_001', 'TXN_BILL_003', 'water_bill',    'failed',        'WATER_001', 'KH556677', 310000, now())
ON CONFLICT (provider_ref_id) DO UPDATE SET
  transaction_id = EXCLUDED.transaction_id,
  service_type = EXCLUDED.service_type,
  status = EXCLUDED.status,
  bill_code = EXCLUDED.bill_code,
  customer_code = EXCLUDED.customer_code,
  amount = EXCLUDED.amount;

-- ═══════════════════════════════════════════
-- REFUNDS
-- ═══════════════════════════════════════════

INSERT INTO refunds (refund_id, transaction_id, status, amount, reason, created_at, updated_at)
VALUES
  (NULL,          'TXN_TRAIN_001',    'not_requested', NULL, NULL, now(), now()),
  (NULL,          'TXN_TRAIN_002',    'not_requested', NULL, NULL, now(), now()),
  (NULL,          'TXN_TRAIN_003',    'not_requested', NULL, NULL, now(), now()),
  (NULL,          'TXN_BILL_001',     'not_requested', NULL, NULL, now(), now()),
  (NULL,          'TXN_BILL_002',     'not_requested', NULL, NULL, now(), now()),
  (NULL,          'TXN_BILL_003',     'not_requested', NULL, NULL, now(), now()),
  (NULL,          'TXN_CONFLICT_001', 'not_requested', NULL, NULL, now(), now()),
  ('REFUND_001',  'TXN_REFUND_001',   'executed',      450000, 'refund_approved', '2026-05-26T11:00:00Z', '2026-05-26T12:00:00Z')
ON CONFLICT (refund_id) DO UPDATE SET
  transaction_id = EXCLUDED.transaction_id,
  status = EXCLUDED.status,
  amount = EXCLUDED.amount;

-- ═══════════════════════════════════════════
-- RECONCILIATION CASES
-- Only BILL_002 has a mismatch
-- ═══════════════════════════════════════════

INSERT INTO reconciliation_cases (reconciliation_id, transaction_id, status, mismatch_type, details, created_at)
VALUES
  ('RECON_BILL_002', 'TXN_BILL_002', 'wallet_provider_mismatch', 'wallet_debited_provider_not_confirmed', '{}', '2026-05-27T12:30:00Z')
ON CONFLICT (reconciliation_id) DO UPDATE SET
  transaction_id = EXCLUDED.transaction_id,
  status = EXCLUDED.status,
  mismatch_type = EXCLUDED.mismatch_type;
