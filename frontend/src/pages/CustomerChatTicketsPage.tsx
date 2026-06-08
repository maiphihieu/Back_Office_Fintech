/**
 * CustomerChatTicketsPage — back-office dashboard for customer-chat handoff
 * tickets. Streamlined columns for quick decision-making.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  chatTicketsApi,
  type ChatTicketRow,
  type ChatTicketFilters,
} from '../api/chatTickets';

const WORKFLOWS = [
  'wallet_topup',
  'train_ticket',
  'utility_bill',
  'fraud_account_lock',
  'merchant_settlement_delay',
];
const STATUSES = [
  'pending_review',
  'pending_approval',
  'need_more_info',
  'closed_no_action',
  'approved',
];
const RISKS = ['low', 'medium', 'high', 'critical', 'unknown'];

const WORKFLOW_LABELS: Record<string, string> = {
  wallet_topup: 'Nạp tiền ví',
  train_ticket: 'Vé tàu',
  utility_bill: 'Hóa đơn',
  fraud_account_lock: 'Khóa TK',
  merchant_settlement_delay: 'Giải ngân',
};

const STATUS_LABELS: Record<string, { text: string; tone: string }> = {
  pending_review: { text: 'Chờ xử lý', tone: 'staff' },
  pending_approval: { text: 'Chờ duyệt', tone: 'approval' },
  need_more_info: { text: 'Cần thêm TT', tone: 'chat' },
  closed_no_action: { text: 'Đã đóng', tone: 'muted' },
  approved: { text: 'Đã duyệt', tone: 'wallet' },
};

const RISK_TONES: Record<string, string> = {
  low: 'wallet',
  medium: 'merchant',
  high: 'approval',
  critical: 'approval',
};

function Badge({ text, tone }: { text: string; tone: string }) {
  const tones: Record<string, React.CSSProperties> = {
    chat: { background: '#e0f2fe', color: '#075985' },
    merchant: { background: '#fef3c7', color: '#92400e' },
    wallet: { background: '#dcfce7', color: '#166534' },
    approval: { background: '#fee2e2', color: '#991b1b' },
    staff: { background: '#ede9fe', color: '#5b21b6' },
    muted: { background: '#f1f5f9', color: '#475569' },
  };
  return (
    <span
      style={{
        ...(tones[tone] || tones.muted),
        padding: '2px 8px',
        borderRadius: 999,
        fontSize: '0.72rem',
        fontWeight: 600,
        whiteSpace: 'nowrap',
        marginRight: 4,
      }}
    >
      {text}
    </span>
  );
}

function relativeTime(iso: string) {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'Vừa xong';
  if (mins < 60) return `${mins}p trước`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h trước`;
  const days = Math.floor(hours / 24);
  return `${days}d trước`;
}

export default function CustomerChatTicketsPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<ChatTicketRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [filters, setFilters] = useState<ChatTicketFilters>({ source: 'customer_chat' });
  const [search, setSearch] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await chatTicketsApi.list({ ...filters, q: search || undefined });
      setRows(res.tickets);
      setTotal(res.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Lỗi tải dữ liệu');
    } finally {
      setLoading(false);
    }
  }, [filters, search]);

  useEffect(() => {
    load();
  }, [load]);

  const setFilter = (key: keyof ChatTicketFilters, value: string) =>
    setFilters((f) => ({ ...f, [key]: value === '' ? undefined : value }));

  const inputStyle: React.CSSProperties = {
    padding: '6px 8px',
    borderRadius: 6,
    border: '1px solid #cbd5e1',
    fontSize: '0.85rem',
  };

  const headers = useMemo(
    () => [
      'Ticket', 'Người khiếu nại', 'Loại', 'Workflow',
      'Action đề xuất', 'Rủi ro', 'Trạng thái', 'Cập nhật',
    ],
    [],
  );

  return (
    <div style={{ padding: '20px 24px' }}>
      <h1 style={{ fontSize: '1.4rem', marginBottom: 4 }}>Customer Chat Tickets</h1>
      <p style={{ color: '#64748b', marginBottom: 16, fontSize: '0.9rem' }}>
        Ticket từ chat khách hàng. Tổng: {total}.
      </p>

      {/* Filters */}
      <div
        style={{
          display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16,
          alignItems: 'center',
        }}
      >
        <select style={inputStyle} value={filters.source ?? 'customer_chat'}
          onChange={(e) => setFilter('source', e.target.value)}>
          <option value="customer_chat">Source: Customer Chat</option>
          <option value="staff">Source: Staff Created</option>
          <option value="all">Source: All</option>
        </select>
        <select style={inputStyle} value={filters.subject_type ?? ''}
          onChange={(e) => setFilter('subject_type', e.target.value)}>
          <option value="">Loại: Tất cả</option>
          <option value="wallet_user">Wallet User</option>
          <option value="merchant">Merchant</option>
        </select>
        <select style={inputStyle} value={filters.workflow ?? ''}
          onChange={(e) => setFilter('workflow', e.target.value)}>
          <option value="">Workflow: Tất cả</option>
          {WORKFLOWS.map((w) => <option key={w} value={w}>{WORKFLOW_LABELS[w] || w}</option>)}
        </select>
        <select style={inputStyle} value={filters.status ?? ''}
          onChange={(e) => setFilter('status', e.target.value)}>
          <option value="">Trạng thái: Tất cả</option>
          {STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABELS[s]?.text || s}</option>)}
        </select>
        <select style={inputStyle} value={filters.risk_level ?? ''}
          onChange={(e) => setFilter('risk_level', e.target.value)}>
          <option value="">Rủi ro: Tất cả</option>
          {RISKS.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <select style={inputStyle}
          value={filters.approval_required === undefined ? '' : String(filters.approval_required)}
          onChange={(e) => setFilter('approval_required', e.target.value)}>
          <option value="">Phê duyệt: Tất cả</option>
          <option value="true">Cần phê duyệt</option>
          <option value="false">Không cần</option>
        </select>
        <input style={{ ...inputStyle, minWidth: 220 }}
          placeholder="Tìm SĐT / email / user_id / merchant_id / mã GD"
          value={search} onChange={(e) => setSearch(e.target.value)} />
        <button style={{ ...inputStyle, cursor: 'pointer', background: '#0ea5e9', color: '#fff', border: 'none' }}
          onClick={load}>Lọc</button>
      </div>

      {error && <div style={{ color: '#b91c1c', marginBottom: 12 }}>{error}</div>}
      {loading && <div style={{ color: '#64748b' }}>Đang tải…</div>}

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.83rem' }}>
          <thead>
            <tr style={{ background: '#f8fafc', textAlign: 'left' }}>
              {headers.map((h) => (
                <th key={h} style={{ padding: '8px 10px', borderBottom: '2px solid #e2e8f0', whiteSpace: 'nowrap' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => {
              const st = STATUS_LABELS[t.backoffice_ticket_status] || { text: t.backoffice_ticket_status, tone: 'muted' };
              const riskTone = RISK_TONES[t.risk_level] || 'muted';
              return (
                <tr key={t.ticket_id}
                  style={{ borderBottom: '1px solid #eef2f7', cursor: 'pointer' }}
                  onClick={() => navigate(`/chat-tickets/${t.ticket_id}`)}>
                  <td style={{ padding: '8px 10px', fontFamily: 'monospace', fontSize: '0.78rem' }}>{t.ticket_id}</td>
                  <td style={{ padding: '8px 10px' }}>
                    <div>{t.complainant_display_name || '—'}</div>
                    {t.complainant_phone && (
                      <span style={{ fontSize: '0.72rem', color: '#94a3b8' }}>{t.complainant_phone}</span>
                    )}
                  </td>
                  <td style={{ padding: '8px 10px' }}>
                    {t.subject_type === 'merchant'
                      ? <Badge text="Merchant" tone="merchant" />
                      : t.subject_type === 'wallet_user'
                        ? <Badge text="Wallet" tone="wallet" />
                        : <Badge text={t.subject_type || '—'} tone="muted" />}
                  </td>
                  <td style={{ padding: '8px 10px' }}>
                    <Badge text={WORKFLOW_LABELS[t.selected_workflow] || t.selected_workflow || '—'} tone="chat" />
                  </td>
                  <td style={{ padding: '8px 10px', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {t.recommended_action || '—'}
                  </td>
                  <td style={{ padding: '8px 10px' }}>
                    <Badge text={t.risk_level} tone={riskTone} />
                  </td>
                  <td style={{ padding: '8px 10px' }}>
                    {t.approval_required && <Badge text="Cần duyệt" tone="approval" />}
                    <Badge text={st.text} tone={st.tone} />
                  </td>
                  <td style={{ padding: '8px 10px', whiteSpace: 'nowrap', color: '#64748b', fontSize: '0.78rem' }}>
                    {relativeTime(t.updated_at)}
                  </td>
                </tr>
              );
            })}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={headers.length} style={{ padding: 24, textAlign: 'center', color: '#94a3b8' }}>
                Chưa có ticket nào.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

