/* ─── Auth API — Customer wallet login (phone + PIN) ─── */

import { api } from './client';

export interface MockSession {
  session_id: string;
  subject_type: 'wallet_user' | 'merchant';
  display_name: string;
  role: string;
}

export interface MockSessionsListResponse {
  sessions: MockSession[];
}

export interface CustomerLoginResponse {
  session_id: string;
  subject_type: string;
  display_name: string;
  role: string;
  is_authenticated: boolean;
  message: string;
}

export interface MeResponse {
  session_id: string;
  subject_type: string;
  display_name: string;
  role: string;
  is_authenticated: boolean;
}

const STORAGE_KEY_SESSION_ID = 'customer_session_id';
const STORAGE_KEY_SESSION = 'customer_session';

export const authApi = {
  /** List available demo sessions (internal/debug — not used by customer login). */
  getSessions: () =>
    api.get<MockSessionsListResponse>('/api/auth/mock-sessions'),

  /** Old mock login with session_id (kept for backward compat / merchant). */
  login: (sessionId: string) =>
    api.post<MockSession>('/api/auth/mock-login', { session_id: sessionId }),

  /** Customer wallet login — phone + PIN. */
  customerLogin: (phone: string, pin: string) =>
    api.post<CustomerLoginResponse>('/api/auth/customer-login', { phone, pin }),

  /** Check current session validity. */
  me: (sessionId: string) =>
    api.get<MeResponse>(`/api/auth/me?session_id=${encodeURIComponent(sessionId)}`),

  // ─── Local Storage Helpers ───

  /** Store session after successful login. */
  storeSession: (session: { session_id: string; display_name: string; subject_type: string; role: string }) => {
    localStorage.setItem(STORAGE_KEY_SESSION_ID, session.session_id);
    localStorage.setItem(STORAGE_KEY_SESSION, JSON.stringify({
      session_id: session.session_id,
      subject_type: session.subject_type,
      display_name: session.display_name,
      role: session.role,
    }));
  },

  /** Get stored session_id. */
  getSessionId: (): string | null =>
    localStorage.getItem(STORAGE_KEY_SESSION_ID),

  /** Get stored session object. */
  getSession: (): MockSession | null => {
    const raw = localStorage.getItem(STORAGE_KEY_SESSION);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as MockSession;
    } catch {
      return null;
    }
  },

  /** Logout — clear localStorage. */
  logout: () => {
    localStorage.removeItem(STORAGE_KEY_SESSION_ID);
    localStorage.removeItem(STORAGE_KEY_SESSION);
  },
};
