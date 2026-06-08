BEGIN;

-- =========================================================
-- MOCK CUSTOMER / MERCHANT LOGIN SESSIONS
-- Purpose:
-- Customer chat widget uses session_id to know who is chatting.
-- This table does NOT replace business data.
-- It only connects customer chat -> existing user/merchant data.
-- =========================================================

CREATE TABLE IF NOT EXISTS public.mock_customer_sessions (
  session_id TEXT PRIMARY KEY,

  -- wallet_user = khách dùng ví
  -- merchant = cửa hàng/merchant
  subject_type TEXT NOT NULL CHECK (subject_type IN ('wallet_user', 'merchant')),

  -- For wallet users
  user_id TEXT,
  wallet_id TEXT,

  -- For merchants
  merchant_id TEXT,
  tax_code TEXT,

  -- Shared profile fields
  phone TEXT,
  email TEXT,
  display_name TEXT,

  -- Auth mock status
  is_authenticated BOOLEAN NOT NULL DEFAULT TRUE,
  role TEXT NOT NULL DEFAULT 'customer',

  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ
);

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_mock_customer_sessions_user_id
ON public.mock_customer_sessions(user_id);

CREATE INDEX IF NOT EXISTS idx_mock_customer_sessions_merchant_id
ON public.mock_customer_sessions(merchant_id);

CREATE INDEX IF NOT EXISTS idx_mock_customer_sessions_phone
ON public.mock_customer_sessions(phone);

CREATE INDEX IF NOT EXISTS idx_mock_customer_sessions_email
ON public.mock_customer_sessions(email);

-- =========================================================
-- WALLET USER SESSIONS
-- These sessions connect customer chat to accounts/transactions.
-- =========================================================

INSERT INTO public.mock_customer_sessions (
  session_id,
  subject_type,
  user_id,
  wallet_id,
  merchant_id,
  tax_code,
  phone,
  email,
  display_name,
  is_authenticated,
  role,
  expires_at
)
VALUES
  -- Top-up pending case
  (
    'demo_customer_topup',
    'wallet_user',
    'U_TOPUP_001',
    'WALLET_TOPUP_001',
    NULL,
    NULL,
    '0981000101',
    'topup.customer@example.com',
    'Khách nạp tiền demo',
    TRUE,
    'customer',
    NOW() + INTERVAL '30 days'
  ),

  -- Train ticket case 1
  (
    'demo_customer_train_001',
    'wallet_user',
    'U001',
    'WALLET_U001',
    NULL,
    NULL,
    '0981000102',
    'train001.customer@example.com',
    'Khách mua vé tàu demo 1',
    TRUE,
    'customer',
    NOW() + INTERVAL '30 days'
  ),

  -- Train ticket case 2
  (
    'demo_customer_train_002',
    'wallet_user',
    'U002',
    'WALLET_U002',
    NULL,
    NULL,
    '0981000103',
    'train002.customer@example.com',
    'Khách mua vé tàu demo 2',
    TRUE,
    'customer',
    NOW() + INTERVAL '30 days'
  ),

  -- Electric bill case
  (
    'demo_customer_bill_001',
    'wallet_user',
    'U003',
    'WALLET_U003',
    NULL,
    NULL,
    '0981000104',
    'bill001.customer@example.com',
    'Khách hóa đơn điện demo',
    TRUE,
    'customer',
    NOW() + INTERVAL '30 days'
  ),

  -- Electric bill provider not confirmed
  (
    'demo_customer_bill_002',
    'wallet_user',
    'U004',
    'WALLET_U004',
    NULL,
    NULL,
    '0981000105',
    'bill002.customer@example.com',
    'Khách hóa đơn điện chưa xác nhận',
    TRUE,
    'customer',
    NOW() + INTERVAL '30 days'
  ),

  -- Water bill failed
  (
    'demo_customer_bill_003',
    'wallet_user',
    'U005',
    'WALLET_U005',
    NULL,
    NULL,
    '0981000106',
    'bill003.customer@example.com',
    'Khách hóa đơn nước demo',
    TRUE,
    'customer',
    NOW() + INTERVAL '30 days'
  ),

  -- Conflict case
  (
    'demo_customer_conflict',
    'wallet_user',
    'U006',
    'WALLET_U006',
    NULL,
    NULL,
    '0981000107',
    'conflict.customer@example.com',
    'Khách conflict dữ liệu demo',
    TRUE,
    'customer',
    NOW() + INTERVAL '30 days'
  ),

  -- Refund case
  (
    'demo_customer_refund',
    'wallet_user',
    'U007',
    'WALLET_U007',
    NULL,
    NULL,
    '0981000108',
    'refund.customer@example.com',
    'Khách refund demo',
    TRUE,
    'customer',
    NOW() + INTERVAL '30 days'
  ),

  -- Fraud false positive
  (
    'demo_customer_fraud_fp',
    'wallet_user',
    'U_FRAUD_FP',
    'WALLET_FRAUD_FP',
    NULL,
    NULL,
    '0981000001',
    'fraud.fp@example.com',
    'Khách bị khóa nhầm demo',
    TRUE,
    'customer',
    NOW() + INTERVAL '30 days'
  ),

  -- Fraud high risk
  (
    'demo_customer_fraud_high',
    'wallet_user',
    'U_FRAUD_HIGH',
    'WALLET_FRAUD_HIGH',
    NULL,
    NULL,
    '0981000002',
    'fraud.high@example.com',
    'Khách fraud risk cao demo',
    TRUE,
    'customer',
    NOW() + INTERVAL '30 days'
  ),

  -- Fraud missing evidence
  (
    'demo_customer_fraud_missing',
    'wallet_user',
    'U_FRAUD_MISSING',
    'WALLET_FRAUD_MISSING',
    NULL,
    NULL,
    '0981000003',
    'fraud.missing@example.com',
    'Khách fraud thiếu dữ liệu demo',
    TRUE,
    'customer',
    NOW() + INTERVAL '30 days'
  )

ON CONFLICT (session_id) DO UPDATE SET
  subject_type = EXCLUDED.subject_type,
  user_id = EXCLUDED.user_id,
  wallet_id = EXCLUDED.wallet_id,
  merchant_id = EXCLUDED.merchant_id,
  tax_code = EXCLUDED.tax_code,
  phone = EXCLUDED.phone,
  email = EXCLUDED.email,
  display_name = EXCLUDED.display_name,
  is_authenticated = EXCLUDED.is_authenticated,
  role = EXCLUDED.role,
  expires_at = EXCLUDED.expires_at;

-- =========================================================
-- MERCHANT SESSIONS
-- These sessions connect customer chat to merchant settlement data.
-- =========================================================

INSERT INTO public.mock_customer_sessions (
  session_id,
  subject_type,
  user_id,
  wallet_id,
  merchant_id,
  tax_code,
  phone,
  email,
  display_name,
  is_authenticated,
  role,
  expires_at
)
VALUES
  (
    'demo_merchant_batch_fail',
    'merchant',
    NULL,
    NULL,
    'MRC_001_BATCH_FAIL',
    '0100000001',
    '0903000001',
    'mrc001@example.com',
    'Cửa hàng Batch Fail',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  ),
  (
    'demo_merchant_batch_not_generated',
    'merchant',
    NULL,
    NULL,
    'MRC_002_BATCH_NOT_GENERATED',
    '0100000002',
    '0903000002',
    'mrc002@example.com',
    'Cửa hàng Batch Chưa Generate',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  ),
  (
    'demo_merchant_invalid_bank',
    'merchant',
    NULL,
    NULL,
    'MRC_003_INVALID_BANK',
    '0100000003',
    '0903000003',
    'mrc003@example.com',
    'Cửa hàng Sai Số Tài Khoản',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  ),
  (
    'demo_merchant_name_mismatch',
    'merchant',
    NULL,
    NULL,
    'MRC_004_NAME_MISMATCH',
    '0100000004',
    '0903000004',
    'mrc004@example.com',
    'Cửa hàng Sai Tên Chủ Tài Khoản',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  ),
  (
    'demo_merchant_bank_pending',
    'merchant',
    NULL,
    NULL,
    'MRC_005_BANK_PENDING',
    '0100000005',
    '0903000005',
    'mrc005@example.com',
    'Cửa hàng Bank Pending',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  ),
  (
    'demo_merchant_success_unc_sent',
    'merchant',
    NULL,
    NULL,
    'MRC_006_SUCCESS_UNC_SENT',
    '0100000006',
    '0903000006',
    'mrc006@example.com',
    'Cửa hàng Đã Giải Ngân Có UNC',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  ),
  (
    'demo_merchant_success_unc_not_sent',
    'merchant',
    NULL,
    NULL,
    'MRC_007_SUCCESS_UNC_NOT_SENT',
    '0100000007',
    '0903000007',
    'mrc007@example.com',
    'Cửa hàng Success Chưa Gửi UNC',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  ),
  (
    'demo_merchant_zero_net',
    'merchant',
    NULL,
    NULL,
    'MRC_008_ZERO_NET',
    '0100000008',
    '0903000008',
    'mrc008@example.com',
    'Cửa hàng Net Bằng 0',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  ),
  (
    'demo_merchant_bank_timeout',
    'merchant',
    NULL,
    NULL,
    'MRC_009_BANK_TIMEOUT',
    '0100000009',
    '0903000009',
    'mrc009@example.com',
    'Cửa hàng Bank Timeout',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  ),
  (
    'demo_merchant_bank_pending_verify',
    'merchant',
    NULL,
    NULL,
    'MRC_010_BANK_PENDING_VERIFY',
    '0100000010',
    '0903000010',
    'mrc010@example.com',
    'Cửa hàng Bank Chờ Xác Minh',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  ),
  (
    'demo_merchant_no_ledger',
    'merchant',
    NULL,
    NULL,
    'MRC_011_NO_LEDGER',
    '0100000011',
    '0903000011',
    'mrc011@example.com',
    'Cửa hàng Không Có Ledger',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  ),
  (
    'demo_merchant_not_due_yet',
    'merchant',
    NULL,
    NULL,
    'MRC_012_NOT_DUE_YET',
    '0100000012',
    '0903000012',
    'mrc012@example.com',
    'Cửa hàng Chưa Đến Hạn D+1',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  ),
  (
    'demo_merchant_amount_mismatch',
    'merchant',
    NULL,
    NULL,
    'MRC_013_AMOUNT_MISMATCH',
    '0100000013',
    '0903000013',
    'mrc013@example.com',
    'Cửa hàng Payout Thiếu Tiền',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  ),
  (
    'demo_merchant_on_hold',
    'merchant',
    NULL,
    NULL,
    'MRC_014_MERCHANT_ON_HOLD',
    '0100000014',
    '0903000014',
    'mrc014@example.com',
    'Cửa hàng Đang Bị Hold',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  ),
  (
    'demo_merchant_bank_inactive',
    'merchant',
    NULL,
    NULL,
    'MRC_015_BANK_INACTIVE',
    '0100000015',
    '0903000015',
    'mrc015@example.com',
    'Cửa hàng Tài Khoản Bank Inactive',
    TRUE,
    'merchant',
    NOW() + INTERVAL '30 days'
  )

ON CONFLICT (session_id) DO UPDATE SET
  subject_type = EXCLUDED.subject_type,
  user_id = EXCLUDED.user_id,
  wallet_id = EXCLUDED.wallet_id,
  merchant_id = EXCLUDED.merchant_id,
  tax_code = EXCLUDED.tax_code,
  phone = EXCLUDED.phone,
  email = EXCLUDED.email,
  display_name = EXCLUDED.display_name,
  is_authenticated = EXCLUDED.is_authenticated,
  role = EXCLUDED.role,
  expires_at = EXCLUDED.expires_at;

-- =========================================================
-- NEGATIVE / EDGE SESSIONS FOR TESTING
-- =========================================================

INSERT INTO public.mock_customer_sessions (
  session_id,
  subject_type,
  user_id,
  wallet_id,
  merchant_id,
  tax_code,
  phone,
  email,
  display_name,
  is_authenticated,
  role,
  expires_at
)
VALUES
  -- Not authenticated
  (
    'demo_not_logged_in',
    'wallet_user',
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    'Khách chưa đăng nhập',
    FALSE,
    'customer',
    NOW() + INTERVAL '30 days'
  ),

  -- Expired session
  (
    'demo_expired_session',
    'wallet_user',
    'U_TOPUP_001',
    'WALLET_TOPUP_001',
    NULL,
    NULL,
    '0981000101',
    'expired.customer@example.com',
    'Khách session hết hạn',
    TRUE,
    'customer',
    NOW() - INTERVAL '1 day'
  ),

  -- Logged-in user but intended for ownership mismatch test
  (
    'demo_customer_wrong_owner',
    'wallet_user',
    'U_TOPUP_001',
    'WALLET_TOPUP_001',
    NULL,
    NULL,
    '0981000199',
    'wrong.owner@example.com',
    'Khách test nhập giao dịch người khác',
    TRUE,
    'customer',
    NOW() + INTERVAL '30 days'
  )

ON CONFLICT (session_id) DO UPDATE SET
  subject_type = EXCLUDED.subject_type,
  user_id = EXCLUDED.user_id,
  wallet_id = EXCLUDED.wallet_id,
  merchant_id = EXCLUDED.merchant_id,
  tax_code = EXCLUDED.tax_code,
  phone = EXCLUDED.phone,
  email = EXCLUDED.email,
  display_name = EXCLUDED.display_name,
  is_authenticated = EXCLUDED.is_authenticated,
  role = EXCLUDED.role,
  expires_at = EXCLUDED.expires_at;

COMMIT;

-- =========================================================
-- QUICK CHECK 1: show all sessions
-- =========================================================

SELECT
  session_id,
  subject_type,
  user_id,
  merchant_id,
  wallet_id,
  phone,
  email,
  display_name,
  is_authenticated,
  role,
  expires_at
FROM public.mock_customer_sessions
ORDER BY subject_type, session_id;

-- =========================================================
-- QUICK CHECK 2: wallet customer owns TXN_TOPUP_001?
-- =========================================================

SELECT
  s.session_id,
  s.user_id AS session_user_id,
  t.transaction_id,
  t.user_id AS transaction_user_id,
  t.amount,
  t.status,
  CASE
    WHEN s.user_id = t.user_id THEN 'MATCH'
    ELSE 'NOT_MATCH'
  END AS ownership_check
FROM public.mock_customer_sessions s
JOIN public.transactions t
  ON t.transaction_id = 'TXN_TOPUP_001'
WHERE s.session_id = 'demo_customer_topup';

-- =========================================================
-- QUICK CHECK 3: wallet customer tries another user's transaction?
-- =========================================================

SELECT
  s.session_id,
  s.user_id AS session_user_id,
  t.transaction_id,
  t.user_id AS transaction_user_id,
  t.amount,
  t.status,
  CASE
    WHEN s.user_id = t.user_id THEN 'MATCH'
    ELSE 'NOT_MATCH'
  END AS ownership_check
FROM public.mock_customer_sessions s
JOIN public.transactions t
  ON t.transaction_id = 'TXN_TRAIN_001'
WHERE s.session_id = 'demo_customer_topup';

-- =========================================================
-- QUICK CHECK 4: merchant session connects to settlement data
-- =========================================================

SELECT
  s.session_id,
  s.merchant_id AS session_merchant_id,
  m.merchant_name,
  m.tax_code,
  mba.verification_status AS bank_account_status,
  mba.is_active AS bank_account_active,
  l.net_settlement_amount,
  l.status AS ledger_status,
  p.status AS payout_status,
  b.status AS batch_status,
  r.bank_status,
  r.unc_number
FROM public.mock_customer_sessions s
JOIN public.merchants m
  ON m.merchant_id = s.merchant_id
LEFT JOIN public.merchant_bank_accounts mba
  ON mba.merchant_id = m.merchant_id
LEFT JOIN public.merchant_settlement_ledgers l
  ON l.merchant_id = m.merchant_id
LEFT JOIN public.merchant_payouts p
  ON p.merchant_id = m.merchant_id
  AND p.settlement_date = l.settlement_date
LEFT JOIN public.settlement_batches b
  ON b.batch_id = p.batch_id
LEFT JOIN public.bank_transfer_receipts r
  ON r.bank_transfer_ref = p.bank_transfer_ref
WHERE s.session_id = 'demo_merchant_batch_fail';
