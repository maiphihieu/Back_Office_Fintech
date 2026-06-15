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
  return (
    <span className={`ct-badge ct-badge-${tone}`}>
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

  // eslint-disable-next-line react-hooks/set-state-in-effect -- data fetch on filter/search change
  useEffect(() => { void load(); }, [load]);

  const setFilter = (key: keyof ChatTicketFilters, value: string) =>
    setFilters((f) => ({ ...f, [key]: value === '' ? undefined : value }));

  const headers = useMemo(
    () => [
      'Ticket', 'Người khiếu nại', 'Loại', 'Workflow',
      'Action đề xuất', 'Rủi ro', 'Trạng thái', 'Cập nhật',
    ],
    [],
  );

  return (
    <div className="ct-page">
      <div className="ct-header">
        <div className="ct-title-wrap">
          <h1>Customer Chat Tickets</h1>
          <p className="ct-subtitle">Ticket từ chat khách hàng.</p>
        </div>
        <span className="ct-total-pill">Tổng: {total}</span>
      </div>

      {/* Filters */}
      <div className="filters-bar ct-filters">
        <select className="form-select" value={filters.source ?? 'customer_chat'}
          onChange={(e) => setFilter('source', e.target.value)}>
          <option value="customer_chat">Source: Customer Chat</option>
          <option value="staff">Source: Staff Created</option>
          <option value="all">Source: All</option>
        </select>
        <select className="form-select" value={filters.subject_type ?? ''}
          onChange={(e) => setFilter('subject_type', e.target.value)}>
          <option value="">Loại: Tất cả</option>
          <option value="wallet_user">Wallet User</option>
          <option value="merchant">Merchant</option>
        </select>
        <select className="form-select" value={filters.workflow ?? ''}
          onChange={(e) => setFilter('workflow', e.target.value)}>
          <option value="">Workflow: Tất cả</option>
          {WORKFLOWS.map((w) => <option key={w} value={w}>{WORKFLOW_LABELS[w] || w}</option>)}
        </select>
        <select className="form-select" value={filters.status ?? ''}
          onChange={(e) => setFilter('status', e.target.value)}>
          <option value="">Trạng thái: Tất cả</option>
          {STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABELS[s]?.text || s}</option>)}
        </select>
        <select className="form-select" value={filters.risk_level ?? ''}
          onChange={(e) => setFilter('risk_level', e.target.value)}>
          <option value="">Rủi ro: Tất cả</option>
          {RISKS.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <select className="form-select"
          value={filters.approval_required === undefined ? '' : String(filters.approval_required)}
          onChange={(e) => setFilter('approval_required', e.target.value)}>
          <option value="">Phê duyệt: Tất cả</option>
          <option value="true">Cần phê duyệt</option>
          <option value="false">Không cần</option>
        </select>
        <input className="form-input ct-search"
          placeholder="Tìm SĐT / email / user_id / merchant_id / mã GD"
          value={search} onChange={(e) => setSearch(e.target.value)} />
        <button className="btn btn-primary" onClick={load}>Lọc</button>
      </div>

      {error && <div className="alert alert-danger ct-error">{error}</div>}
      {loading && <div className="loading"><div className="spinner" /></div>}

      <div className="card ct-table-card">
        <div className="table-container">
          <table className="table ct-table">
            <thead>
              <tr>
                {headers.map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => {
                const st = STATUS_LABELS[t.backoffice_ticket_status] || { text: t.backoffice_ticket_status, tone: 'muted' };
                const riskTone = RISK_TONES[t.risk_level] || 'muted';
                return (
                  <tr key={t.ticket_id}
                    onClick={() => navigate(`/chat-tickets/${t.ticket_id}`)}>
                    <td><span className="ct-ticket-id">{t.ticket_id}</span></td>
                    <td>
                      <div className="ct-person">
                        <div>{t.complainant_display_name || '—'}</div>
                        {t.complainant_phone && (
                          <span className="ct-phone">{t.complainant_phone}</span>
                        )}
                      </div>
                    </td>
                    <td>
                      {t.subject_type === 'merchant'
                        ? <Badge text="Merchant" tone="merchant" />
                        : t.subject_type === 'wallet_user'
                          ? <Badge text="Wallet" tone="wallet" />
                          : <Badge text={t.subject_type || '—'} tone="muted" />}
                    </td>
                    <td>
                      <Badge text={WORKFLOW_LABELS[t.selected_workflow] || t.selected_workflow || '—'} tone="chat" />
                    </td>
                    <td className="ct-action-cell">
                      {t.recommended_action || '—'}
                    </td>
                    <td>
                      <Badge text={t.risk_level} tone={riskTone} />
                    </td>
                    <td>
                      {t.approval_required && <Badge text="Cần duyệt" tone="approval" />}
                      <Badge text={st.text} tone={st.tone} />
                    </td>
                    <td className="ct-updated">
                      {relativeTime(t.updated_at)}
                    </td>
                  </tr>
                );
              })}
              {!loading && rows.length === 0 && (
                <tr><td colSpan={headers.length} className="ct-empty-cell">
                  Chưa có ticket nào.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
