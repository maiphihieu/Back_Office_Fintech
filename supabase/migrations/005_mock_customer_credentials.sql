BEGIN;

-- =========================================================
-- MOCK CUSTOMER CREDENTIALS
-- Login bằng số điện thoại + mã PIN ví
-- Source of truth: public.mock_customer_sessions
-- =========================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS public.mock_customer_credentials (
  credential_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Link tới session đã có
  session_id TEXT NOT NULL REFERENCES public.mock_customer_sessions(session_id) ON DELETE CASCADE,

  -- Login bằng số điện thoại
  phone TEXT NOT NULL UNIQUE,

  -- Không lưu PIN plain text
  pin_hash TEXT NOT NULL,

  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  failed_attempts INT NOT NULL DEFAULT 0,
  locked_until TIMESTAMPTZ,

  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT uq_mock_customer_credentials_session_id UNIQUE (session_id)
);

CREATE INDEX IF NOT EXISTS idx_mock_customer_credentials_phone
ON public.mock_customer_credentials(phone);

CREATE INDEX IF NOT EXISTS idx_mock_customer_credentials_session_id
ON public.mock_customer_credentials(session_id);

-- =========================================================
-- INSERT / UPDATE PIN MOCK
-- Tự lấy đúng session_id + phone từ mock_customer_sessions
-- Demo PIN cho tất cả wallet_user = 123456
-- =========================================================

INSERT INTO public.mock_customer_credentials (
  session_id,
  phone,
  pin_hash,
  is_active,
  updated_at
)
SELECT
  s.session_id,
  s.phone,
  crypt('123456', gen_salt('bf')) AS pin_hash,
  TRUE AS is_active,
  NOW() AS updated_at
FROM public.mock_customer_sessions s
WHERE s.subject_type = 'wallet_user'
  AND s.is_authenticated = TRUE
  AND s.phone IS NOT NULL
  AND (s.expires_at IS NULL OR s.expires_at > NOW())
ON CONFLICT (phone) DO UPDATE SET
  session_id = EXCLUDED.session_id,
  pin_hash = EXCLUDED.pin_hash,
  is_active = EXCLUDED.is_active,
  updated_at = NOW();

COMMIT;

-- =========================================================
-- CHECK: Kiểm tra đã tạo credential và PIN đúng chưa
-- =========================================================

SELECT
  c.session_id,
  c.phone,
  s.user_id,
  s.wallet_id,
  s.display_name,
  CASE
    WHEN c.pin_hash = crypt('123456', c.pin_hash) THEN 'PIN_OK'
    ELSE 'PIN_WRONG'
  END AS pin_check
FROM public.mock_customer_credentials c
JOIN public.mock_customer_sessions s
  ON s.session_id = c.session_id
ORDER BY c.session_id;
