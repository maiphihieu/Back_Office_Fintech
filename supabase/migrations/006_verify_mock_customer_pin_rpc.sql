-- =========================================================
-- RPC: verify_mock_customer_pin
--
-- Verifies phone + PIN against mock_customer_credentials,
-- returns ONLY safe public session fields.
--
-- SECURITY:
--   - Never returns pin_hash, user_id, wallet_id, merchant_id,
--     tax_code, phone, email.
--   - Returns empty result if phone not found (does not reveal
--     whether phone exists).
--   - Checks is_active, locked_until, is_authenticated, expires_at.
-- =========================================================

CREATE OR REPLACE FUNCTION public.verify_mock_customer_pin(
  input_phone TEXT,
  input_pin TEXT
)
RETURNS TABLE (
  session_id TEXT,
  subject_type TEXT,
  display_name TEXT,
  role TEXT,
  is_authenticated BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions
AS $$
BEGIN
  RETURN QUERY
  SELECT
    s.session_id,
    s.subject_type,
    s.display_name,
    s.role,
    s.is_authenticated
  FROM public.mock_customer_credentials c
  JOIN public.mock_customer_sessions s
    ON s.session_id = c.session_id
  WHERE c.phone = input_phone
    AND c.is_active = TRUE
    AND (c.locked_until IS NULL OR c.locked_until < NOW())
    AND c.pin_hash = crypt(input_pin, c.pin_hash)
    AND s.is_authenticated = TRUE
    AND s.subject_type = 'wallet_user'
    AND (s.expires_at IS NULL OR s.expires_at > NOW())
  LIMIT 1;
END;
$$;
