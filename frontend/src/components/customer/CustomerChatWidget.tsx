/**
 * CustomerChatWidget — Floating customer support chat bubble.
 *
 * This component is completely SEPARATED from the back-office UI.
 * It renders as a floating icon at bottom-right. When clicked, it
 * opens a popup chat panel for customers to submit complaints.
 *
 * LOGIN GATE:
 *   - Before opening chat, checks localStorage for customer_session_id.
 *   - If not logged in, shows a login prompt with link to /customer-login.
 *   - When sending messages, includes session_id in request body.
 *   - Shows logged-in user name in header.
 *   - Provides logout button.
 *
 * CHAT PERSISTENCE:
 *   - Messages are preserved in localStorage for 3 minutes after last activity.
 *   - Closing the popup does NOT clear messages — only hides the panel.
 *   - Reopening within 3 minutes restores the conversation.
 *   - After 3 minutes, the conversation resets with a fresh greeting.
 *   - Session isolation: changing customer clears old chat.
 *
 * SECURITY:
 *   - Only calls POST /api/customer-chat (sanitized endpoint).
 *   - Only renders: public_response, public_case_id, missing_info_questions.
 *   - Does NOT import or render any back-office components.
 *   - Does NOT expose: evidence_bundle, rule_decision, approval_packet,
 *     action_draft, risk_score, fraud signals, MCP tool results.
 *   - Does NOT send user_id or merchant_id from frontend.
 *   - Does NOT persist PIN, OTP, password, or internal evidence.
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import {
  customerChatApi,
  type CustomerChatResponse,
} from '../../api/customerChat';
import { authApi, type MockSession } from '../../api/authApi';
import './CustomerChatWidget.css';

// ─── Types ───────────────────────────────────────────────────

interface ChatMessage {
  id: string;
  role: 'bot' | 'user' | 'system';
  text: string;
  caseId?: string;
  questions?: string[];
}

// ─── Constants ───────────────────────────────────────────────

const CHAT_TTL_MS = 3 * 60 * 1000; // 3 minutes

const STORAGE_KEYS = {
  messages: 'customer_chat_messages',
  lastActive: 'customer_chat_last_active_at',
  caseId: 'customer_chat_case_id',
  sessionId: 'customer_chat_session_id',
} as const;

const WELCOME_LOGGED_IN: ChatMessage = {
  id: 'welcome',
  role: 'bot',
  text: 'Xin chào! Bạn đã đăng nhập. Hãy nhập nội dung khiếu nại. Vui lòng không cung cấp mật khẩu, OTP hoặc mã PIN.',
};

const EXPIRED_NOTE: ChatMessage = {
  id: 'expired-note',
  role: 'system',
  text: 'Phiên chat trước đã hết hạn, chúng tôi đã bắt đầu cuộc trò chuyện mới.',
};

// ─── Chat Persistence Helpers ────────────────────────────────

/** Save current chat state to localStorage (public data only). */
function saveChatState(
  messages: ChatMessage[],
  sessionId: string | null,
  caseId?: string,
): void {
  try {
    // Only store safe public message fields — never internal evidence
    const safeMessages = messages.map((m) => ({
      id: m.id,
      role: m.role,
      text: m.text,
      caseId: m.caseId,
      questions: m.questions,
    }));
    localStorage.setItem(STORAGE_KEYS.messages, JSON.stringify(safeMessages));
    localStorage.setItem(STORAGE_KEYS.lastActive, String(Date.now()));
    if (sessionId) {
      localStorage.setItem(STORAGE_KEYS.sessionId, sessionId);
    }
    if (caseId) {
      localStorage.setItem(STORAGE_KEYS.caseId, caseId);
    }
  } catch {
    // Storage full or unavailable — silently ignore
  }
}

/** Load chat state from localStorage. Returns null if expired or mismatched. */
function loadChatState(currentSessionId: string | null): {
  messages: ChatMessage[];
  caseId: string;
  expired: boolean;
} | null {
  try {
    const storedSessionId = localStorage.getItem(STORAGE_KEYS.sessionId);
    const lastActiveStr = localStorage.getItem(STORAGE_KEYS.lastActive);
    const messagesStr = localStorage.getItem(STORAGE_KEYS.messages);

    // Session isolation: different customer → don't restore
    if (!currentSessionId || storedSessionId !== currentSessionId) {
      clearChatState();
      return null;
    }

    if (!lastActiveStr || !messagesStr) {
      return null;
    }

    const lastActive = parseInt(lastActiveStr, 10);
    const elapsed = Date.now() - lastActive;

    // TTL expired
    if (elapsed > CHAT_TTL_MS) {
      clearChatState();
      return { messages: [], caseId: '', expired: true };
    }

    const messages: ChatMessage[] = JSON.parse(messagesStr);
    const caseId = localStorage.getItem(STORAGE_KEYS.caseId) || '';

    return { messages, caseId, expired: false };
  } catch {
    clearChatState();
    return null;
  }
}

/** Clear all chat persistence keys. */
function clearChatState(): void {
  localStorage.removeItem(STORAGE_KEYS.messages);
  localStorage.removeItem(STORAGE_KEYS.lastActive);
  localStorage.removeItem(STORAGE_KEYS.caseId);
  localStorage.removeItem(STORAGE_KEYS.sessionId);
}

/** Update only the last-active timestamp. */
function touchLastActive(): void {
  try {
    localStorage.setItem(STORAGE_KEYS.lastActive, String(Date.now()));
  } catch {
    // ignore
  }
}

// ─── Component ───────────────────────────────────────────────

export default function CustomerChatWidget() {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [session, setSession] = useState<MockSession | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [activeCaseId, setActiveCaseId] = useState('');

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  // Check auth when popup opens
  useEffect(() => {
    if (!isOpen) return;

    const sessionId = authApi.getSessionId();
    const storedSession = authApi.getSession();

    if (sessionId && storedSession) {
      // Verify session is still valid
      authApi.me(sessionId).then((res) => {
        if (res.is_authenticated) {
          setSession(storedSession);

          // Try to restore persisted chat
          const cached = loadChatState(sessionId);
          if (cached && !cached.expired && cached.messages.length > 0) {
            // Restore previous conversation
            setMessages(cached.messages);
            setActiveCaseId(cached.caseId);
          } else if (cached?.expired) {
            // Expired — finalize the prior chat into a back-office ticket,
            // then show note + fresh greeting.
            customerChatApi
              .handoff(sessionId, 'expired')
              .catch(() => { /* best-effort */ });
            setMessages([EXPIRED_NOTE, WELCOME_LOGGED_IN]);
            setActiveCaseId('');
          } else {
            // No cached chat — fresh greeting
            setMessages([WELCOME_LOGGED_IN]);
            setActiveCaseId('');
          }
          touchLastActive();
        } else {
          // Session expired — clear everything
          authApi.logout();
          clearChatState();
          setSession(null);
          setMessages([]);
          setActiveCaseId('');
        }
        setAuthChecked(true);
      }).catch(() => {
        // Network error — use cached session optimistically
        setSession(storedSession);

        const cached = loadChatState(sessionId);
        if (cached && !cached.expired && cached.messages.length > 0) {
          setMessages(cached.messages);
          setActiveCaseId(cached.caseId);
        } else {
          setMessages([WELCOME_LOGGED_IN]);
          setActiveCaseId('');
        }
        touchLastActive();
        setAuthChecked(true);
      });
    } else {
      setSession(null);
      setMessages([]);
      setActiveCaseId('');
      setAuthChecked(true);
    }
  }, [isOpen]);

  // Focus input when popup opens and auth is checked
  useEffect(() => {
    if (isOpen && authChecked && session) {
      setTimeout(() => inputRef.current?.focus(), 300);
    }
  }, [isOpen, authChecked, session]);

  // ─── Close: save state, hide popup ───
  const handleClose = useCallback(() => {
    // Save current conversation before closing
    const sessionId = authApi.getSessionId();
    if (sessionId && messages.length > 0) {
      saveChatState(messages, sessionId, activeCaseId);
    }
    setAuthChecked(false);
    setIsOpen(false);
  }, [messages, activeCaseId]);

  // ─── Open: restore state or fresh ───
  const handleOpen = useCallback(() => {
    setIsOpen(true);
  }, []);

  const toggleOpen = useCallback(() => {
    if (isOpen) {
      handleClose();
    } else {
      handleOpen();
    }
  }, [isOpen, handleClose, handleOpen]);

  const handleLogout = useCallback(() => {
    // Explicit chat end → finalize ONE back-office ticket (deduped server-side).
    const sessionId = authApi.getSessionId();
    if (sessionId) {
      customerChatApi.handoff(sessionId, 'ended').catch(() => { /* best-effort */ });
    }
    authApi.logout();
    clearChatState();
    setSession(null);
    setMessages([]);
    setActiveCaseId('');
    setAuthChecked(true);
  }, []);

  const handleSubmit = useCallback(async () => {
    const text = inputText.trim();
    if (!text || isLoading) return;

    // Add user message
    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      text,
    };
    const updatedMessages = [...messages, userMsg];
    setMessages(updatedMessages);
    setInputText('');
    setIsLoading(true);

    // Save state on send (activity timestamp)
    const sessionId = authApi.getSessionId();
    saveChatState(updatedMessages, sessionId, activeCaseId);

    try {
      const response: CustomerChatResponse = await customerChatApi.submit({
        message: text,
        session_id: sessionId,
      });

      // Handle need_login response
      if (response.status === 'need_login') {
        authApi.logout();
        clearChatState();
        setSession(null);
        setMessages((prev) => [
          ...prev,
          {
            id: `bot-login-${Date.now()}`,
            role: 'bot',
            text: response.public_response,
          },
        ]);
        setIsLoading(false);
        return;
      }

      // Bot response — only safe public fields
      const botMsg: ChatMessage = {
        id: `bot-${Date.now()}`,
        role: 'bot',
        text: response.public_response,
        caseId: response.public_case_id,
        questions:
          response.missing_info_questions.length > 0
            ? response.missing_info_questions
            : undefined,
      };

      // Track active case
      const newCaseId =
        response.public_case_id && response.public_case_id !== 'pending'
          ? response.public_case_id
          : activeCaseId;
      setActiveCaseId(newCaseId);

      setMessages((prev) => {
        const next = [...prev, botMsg];
        // Save after bot reply
        saveChatState(next, sessionId, newCaseId);
        return next;
      });
    } catch {
      // Safe error message — no internal details
      const errorMsg: ChatMessage = {
        id: `bot-err-${Date.now()}`,
        role: 'bot',
        text: 'Xin lỗi, hệ thống đang bận. Vui lòng thử lại sau ít phút.',
      };
      setMessages((prev) => {
        const next = [...prev, errorMsg];
        saveChatState(next, sessionId, activeCaseId);
        return next;
      });
    } finally {
      setIsLoading(false);
    }
  }, [inputText, isLoading, messages, activeCaseId]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  // ─── Login Prompt (not logged in) ───
  const renderLoginPrompt = () => (
    <div className="ccw-messages" style={{ justifyContent: 'center', alignItems: 'center', textAlign: 'center' }}>
      <div style={{ padding: '24px 16px' }}>
        <div style={{ fontSize: '2rem', marginBottom: '12px' }}>🔒</div>
        <div style={{
          fontSize: '0.88rem',
          color: '#1E293B',
          fontWeight: 600,
          marginBottom: '8px',
        }}>
          Vui lòng đăng nhập
        </div>
        <div style={{
          fontSize: '0.82rem',
          color: '#64748B',
          marginBottom: '16px',
          lineHeight: 1.5,
        }}>
          Vui lòng đăng nhập để hệ thống xác minh tài khoản của bạn.
        </div>
        <a
          href="/customer-login"
          id="customer-chat-login-link"
          style={{
            display: 'inline-block',
            padding: '10px 24px',
            background: 'linear-gradient(135deg, #2563EB 0%, #0EA5E9 100%)',
            color: '#ffffff',
            borderRadius: '10px',
            fontSize: '0.84rem',
            fontWeight: 600,
            textDecoration: 'none',
            transition: 'transform 0.15s ease, box-shadow 0.15s ease',
          }}
        >
          Đăng nhập
        </a>
      </div>
    </div>
  );

  return (
    <>
      {/* ── Floating Action Button ── */}
      <button
        id="customer-chat-fab"
        className="ccw-fab"
        onClick={toggleOpen}
        aria-label="Mở hỗ trợ khách hàng"
        title="Hỗ trợ"
        type="button"
      >
        {isOpen ? (
          <span className="ccw-fab-icon">✕</span>
        ) : (
          <span className="ccw-fab-icon">💬</span>
        )}
      </button>

      {/* ── Chat Popup ── */}
      <div
        id="customer-chat-popup"
        className={`ccw-popup ${isOpen ? 'ccw-popup--open' : ''}`}
        role="dialog"
        aria-label="Trung tâm hỗ trợ"
      >
        {/* Header */}
        <div className="ccw-header">
          <div>
            <div className="ccw-header-title">Trung tâm hỗ trợ</div>
            <div className="ccw-header-subtitle">
              {session
                ? `${session.display_name}`
                : 'Hỗ trợ khiếu nại 24/7'}
            </div>
          </div>
          <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
            {session && (
              <button
                id="customer-chat-logout"
                className="ccw-close-btn"
                onClick={handleLogout}
                aria-label="Đăng xuất"
                title="Đăng xuất"
                type="button"
                style={{ fontSize: '0.65rem', fontWeight: 600 }}
              >
                ↪
              </button>
            )}
            <button
              id="customer-chat-close"
              className="ccw-close-btn"
              onClick={toggleOpen}
              aria-label="Đóng"
              title="Đóng"
              type="button"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Content — Login prompt or chat */}
        {authChecked && !session ? (
          renderLoginPrompt()
        ) : (
          <>
            {/* Messages */}
            <div className="ccw-messages" id="customer-chat-messages">
              {messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`ccw-msg-row ${
                    msg.role === 'user'
                      ? 'ccw-msg-row--user'
                      : msg.role === 'system'
                        ? 'ccw-msg-row--system'
                        : 'ccw-msg-row--bot'
                  }`}
                >
                  <div
                    className={`ccw-bubble ccw-bubble--${msg.role}`}
                  >
                    {msg.text}
                  </div>

                  {/* Case ID badge */}
                  {msg.caseId && msg.caseId !== 'pending' && (
                    <div className="ccw-case-id">
                      Mã tra cứu: {msg.caseId}
                    </div>
                  )}

                  {/* Missing info questions */}
                  {msg.questions && msg.questions.length > 0 && (
                    <div className="ccw-questions">
                      <div className="ccw-questions-title">
                        Để xử lý nhanh hơn, vui lòng bổ sung:
                      </div>
                      <ul>
                        {msg.questions.map((q, i) => (
                          <li key={i}>{q}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              ))}

              {/* Loading state */}
              {isLoading && (
                <div className="ccw-loading">
                  <div className="ccw-loading-dots">
                    <span />
                    <span />
                    <span />
                  </div>
                  Đang ghi nhận yêu cầu...
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>

            {/* Input Area */}
            <div className="ccw-input-area">
              <textarea
                id="customer-chat-input"
                className="ccw-input"
                ref={inputRef}
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Nhập nội dung khiếu nại của bạn..."
                rows={1}
                disabled={isLoading || !session}
              />
              <button
                id="customer-chat-send"
                className="ccw-send-btn"
                onClick={handleSubmit}
                disabled={!inputText.trim() || isLoading || !session}
                type="button"
              >
                Gửi
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
