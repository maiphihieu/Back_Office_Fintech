/**
 * CustomerLoginPage — Customer & merchant login with phone + PIN.
 *
 * This page is SEPARATE from the back-office UI.
 * Wallet customers AND merchants authenticate with phone number + PIN.
 * The subject_type (wallet_user | merchant) is resolved server-side by the
 * verify_mock_customer_pin RPC — the UI is identical for both.
 *
 * SECURITY:
 *   - PIN is sent once to backend, NEVER stored in localStorage.
 *   - Does NOT show demo session cards or internal session_ids.
 *   - Does NOT show user_id, wallet_id, merchant_id, tax_code.
 *   - Does NOT import any back-office components.
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { authApi } from '../api/authApi';
import './CustomerLoginPage.css';

export default function CustomerLoginPage() {
  const navigate = useNavigate();
  const [phone, setPhone] = useState('');
  const [pin, setPin] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const phoneRef = useRef<HTMLInputElement>(null);

  // Focus phone input on mount
  useEffect(() => {
    phoneRef.current?.focus();
  }, []);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const trimmedPhone = phone.trim();
      const trimmedPin = pin.trim();

      if (!trimmedPhone || !trimmedPin) {
        setError('Vui lòng nhập đầy đủ số điện thoại và mã PIN.');
        return;
      }

      setIsLoading(true);
      setError(null);

      try {
        const result = await authApi.customerLogin(trimmedPhone, trimmedPin);

        if (result.is_authenticated) {
          // Store session — only session_id + display_name, NEVER PIN
          authApi.storeSession({
            session_id: result.session_id,
            display_name: result.display_name,
            subject_type: result.subject_type,
            role: result.role,
          });
          navigate('/');
        } else {
          setError(result.message || 'Số điện thoại hoặc mã PIN không đúng.');
        }
      } catch {
        setError('Số điện thoại hoặc mã PIN không đúng.');
      } finally {
        setIsLoading(false);
        // Clear PIN from state after submit
        setPin('');
      }
    },
    [phone, pin, navigate],
  );

  return (
    <div className="cl-page">
      <div className="cl-container">
        {/* Header */}
        <div className="cl-header">
          <div className="cl-logo">💳</div>
          <h1 className="cl-title">Đăng nhập</h1>
          <p className="cl-subtitle">
            Đăng nhập để hệ thống xác minh tài khoản và hỗ trợ nhanh hơn.
          </p>
        </div>

        {/* Login Form */}
        <form className="cl-form" onSubmit={handleSubmit} autoComplete="off">
          {/* Phone */}
          <div className="cl-field">
            <label htmlFor="customer-login-phone" className="cl-label">
              Số điện thoại
            </label>
            <input
              id="customer-login-phone"
              ref={phoneRef}
              className="cl-input"
              type="tel"
              inputMode="numeric"
              placeholder="Nhập số điện thoại"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              disabled={isLoading}
              autoComplete="off"
              maxLength={15}
            />
          </div>

          {/* PIN */}
          <div className="cl-field">
            <label htmlFor="customer-login-pin" className="cl-label">
              Mã PIN
            </label>
            <input
              id="customer-login-pin"
              className="cl-input cl-input--pin"
              type="password"
              inputMode="numeric"
              placeholder="Nhập mã PIN"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              disabled={isLoading}
              autoComplete="off"
              maxLength={10}
            />
          </div>

          {/* Error */}
          {error && (
            <div className="cl-error" role="alert">
              {error}
            </div>
          )}

          {/* Submit */}
          <button
            id="customer-login-submit"
            className="cl-submit-btn"
            type="submit"
            disabled={isLoading || !phone.trim() || !pin.trim()}
          >
            {isLoading ? (
              <>
                <span className="cl-loading-spinner-inline">
                  <span />
                  <span />
                  <span />
                </span>
                Đang xác thực...
              </>
            ) : (
              'Đăng nhập'
            )}
          </button>
        </form>

        {/* Demo hint — no demo account list */}
        <div className="cl-demo-hint">
          Đây là môi trường demo. Vui lòng dùng số điện thoại và mã PIN đã được
          cấp.
        </div>

        {/* Footer */}
        <div className="cl-footer">
          <a href="/">← Quay về trang chính</a>
        </div>
      </div>
    </div>
  );
}
