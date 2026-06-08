/**
 * ChatTicketDetailPage — Redesigned for action-oriented staff UX.
 *
 * Layout: 2-column (65/35) with sticky right action panel.
 * Staff can understand the issue and decide within 10 seconds.
 *
 * Left column:
 *   A. Người khiếu nại (complainant)
 *   B. Vấn đề khách phản ánh (structured customer problem)
 *   C. Agent Diagnosis (staff-readable)
 *   D. Evidence Summary (checklist cards)
 *   E. Conversation Timeline (last 5 + expand)
 *   F. Chi tiết kỹ thuật (collapsed)
 *
 * Right column (sticky):
 *   "Việc nhân viên cần làm" action panel with approve/reject/request-info
 */

import { useCallback, useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { chatTicketsApi, type ChatTicketDetail } from '../api/chatTickets';
import ApproveConfirmModal from '../components/ApproveConfirmModal';

const STAFF_ACTOR = 'back_office_staff';

/* ─── Workflow display names ─── */
const WORKFLOW_LABELS: Record<string, string> = {
  wallet_topup: 'Nạp tiền ví',
  train_ticket: 'Vé tàu',
  utility_bill: 'Hóa đơn tiện ích',
  fraud_account_lock: 'Khóa tài khoản gian lận',
  merchant_settlement_delay: 'Chậm giải ngân Merchant',
};

/* ─── Status display ─── */
const STATUS_MAP: Record<string, { label: string; cls: string }> = {
  pending_review: { label: 'Chờ xử lý', cls: 'badge-amber' },
  pending_approval: { label: 'Chờ phê duyệt', cls: 'badge-red' },
  need_more_info: { label: 'Cần thêm thông tin', cls: 'badge-blue' },
  closed_no_action: { label: 'Đã đóng', cls: 'badge-neutral' },
  approved: { label: 'Đã duyệt', cls: 'badge-green' },
};

/* ─── Risk display ─── */
const RISK_MAP: Record<string, { label: string; cls: string }> = {
  low: { label: '🟢 Thấp', cls: 'badge-green' },
  medium: { label: '🟡 Trung bình', cls: 'badge-amber' },
  high: { label: '🔴 Cao', cls: 'badge-red' },
  critical: { label: '🔴 Rất cao', cls: 'badge-red' },
  unknown: { label: 'Chưa rõ', cls: 'badge-neutral' },
};

/* ─── Evidence status icons ─── */
const EV_ICONS: Record<string, string> = {
  checked: '✅',
  missing: '⚠️',
  needs_review: '🔍',
};

const EV_STATUS_LABELS: Record<string, string> = {
  checked: 'Đã kiểm tra',
  missing: 'Thiếu',
  needs_review: 'Cần xem xét',
};

/* ─── Confidence display ─── */
const CONFIDENCE_LABELS: Record<string, { label: string; cls: string }> = {
  high: { label: 'Cao', cls: '' },
  medium: { label: 'Trung bình', cls: 'cht-warning' },
  low: { label: 'Thấp', cls: 'cht-warning' },
};

function formatTime(iso: string) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('vi-VN', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

/* ═══════════════════════════════════════════════════════════════
   MAIN PAGE COMPONENT
   ═══════════════════════════════════════════════════════════════ */

export default function ChatTicketDetailPage() {
  const { ticketId = '' } = useParams();
  const navigate = useNavigate();
  const [t, setT] = useState<ChatTicketDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showAllMessages, setShowAllMessages] = useState(false);
  const [showTechDetails, setShowTechDetails] = useState(false);
  const [showApproveModal, setShowApproveModal] = useState(false);

  const load = useCallback(async () => {
    try {
      setT(await chatTicketsApi.get(ticketId));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Không tải được ticket');
    }
  }, [ticketId]);

  useEffect(() => { load(); }, [load]);

  const decide = async (kind: 'approve' | 'reject' | 'requestInfo') => {
    setBusy(true);
    try {
      const fn = chatTicketsApi[kind];
      setT(await fn(ticketId, STAFF_ACTOR));
      setShowApproveModal(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Thao tác thất bại');
    } finally {
      setBusy(false);
    }
  };

  if (error) return <div className="cht-error">{error}</div>;
  if (!t) return <div className="cht-loading">Đang tải…</div>;

  /* Derived data */
  const c = t.complainant;
  const header = t.ticket_header;
  const problem = t.customer_problem_structured;
  const diagnosis = t.agent_diagnosis;
  const checklist = t.evidence_checklist || [];
  const action = t.staff_action;
  const isWalletUser = t.subject_type === 'wallet_user';
  const isMerchant = t.subject_type === 'merchant';

  /* Messages to show */
  const allMessages = t.timeline || [];
  const limitedMessages = t.conversation_timeline || allMessages.slice(-5);
  const messagesToShow = showAllMessages ? allMessages : limitedMessages;
  const hasMoreMessages = allMessages.length > limitedMessages.length;

  /* Status/risk/workflow labels */
  const statusInfo = STATUS_MAP[t.backoffice_ticket_status] || { label: t.backoffice_ticket_status, cls: 'badge-neutral' };
  const riskInfo = RISK_MAP[t.risk_level] || RISK_MAP.unknown;
  const workflowLabel = WORKFLOW_LABELS[t.selected_workflow] || t.selected_workflow || '—';
  const confInfo = CONFIDENCE_LABELS[diagnosis?.confidence || 'low'] || CONFIDENCE_LABELS.low;

  return (
    <div className="cht-container">
      {/* ── Back link ── */}
      <button className="cht-back-link" onClick={() => navigate('/chat-tickets')}>
        ← Danh sách ticket
      </button>

      {/* ══════ HEADER CARD ══════ */}
      <div className="cht-header-card">
        <div className="cht-header-top">
          <h1>Chi tiết ticket</h1>
          <code className="cht-ticket-id">{t.ticket_id}</code>
        </div>

        <div className="cht-badges">
          <span className="badge badge-cyan">Customer Chat</span>
          <span className="badge badge-blue">{workflowLabel}</span>
          <span className={`badge ${statusInfo.cls}`}>{statusInfo.label}</span>
          <span className={`badge ${riskInfo.cls}`}>{riskInfo.label}</span>
          {t.approval_required && <span className="badge badge-red">Cần phê duyệt</span>}
        </div>

        {/* One-line summary */}
        {(header?.summary || t.conversation_summary || t.customer_problem) && (
          <div className="cht-summary-line">
            <span className="cht-summary-label">Vấn đề chính</span>
            <span className="cht-summary-value">
              {header?.summary || t.conversation_summary || t.customer_problem}
            </span>
          </div>
        )}

        {/* Time info */}
        <div style={{ display: 'flex', gap: 16, marginTop: 8, fontSize: '0.76rem', color: 'var(--text-muted)' }}>
          {t.created_at && <span>Tạo: {formatTime(t.created_at)}</span>}
          {t.updated_at && <span>Cập nhật: {formatTime(t.updated_at)}</span>}
        </div>
      </div>

      {/* ══════ 2-COLUMN GRID ══════ */}
      <div className="cht-grid">

        {/* ─── LEFT COLUMN (65%) ─── */}
        <div className="cht-left">

          {/* ═══ A. NGƯỜI KHIẾU NẠI ═══ */}
          <div className="cht-section">
            <div className="cht-card">
              <h3 className="cht-card-title">
                <span className="cht-icon">👤</span>
                Người khiếu nại
                {isWalletUser && <span className="badge badge-green" style={{ marginLeft: 'auto' }}>Wallet User</span>}
                {isMerchant && <span className="badge badge-amber" style={{ marginLeft: 'auto' }}>Merchant</span>}
              </h3>

              {isWalletUser && (
                <>
                  {c.display_name && <Row label="Tên" value={c.display_name} />}
                  {c.phone && <Row label="Điện thoại" value={c.phone} />}
                  {c.user_id && <Row label="User ID" value={c.user_id} mono />}
                  {c.wallet_id && <Row label="Wallet ID" value={c.wallet_id} mono />}
                  {c.account_status && <Row label="Trạng thái TK" value={c.account_status} />}
                  {c.wallet_status && <Row label="Trạng thái ví" value={c.wallet_status} />}
                </>
              )}

              {isMerchant && (
                <>
                  {(c.merchant_name || c.display_name) && <Row label="Tên Merchant" value={c.merchant_name || c.display_name} />}
                  {c.phone && <Row label="Điện thoại" value={c.phone} />}
                  {c.merchant_id && <Row label="Merchant ID" value={c.merchant_id} mono />}
                  {c.tax_code && <Row label="Mã số thuế" value={c.tax_code} mono />}
                  {c.bank_account_status && <Row label="TK ngân hàng" value={c.bank_account_status} />}
                  {c.settlement_cycle && <Row label="Chu kỳ settlement" value={c.settlement_cycle} />}
                </>
              )}

              {/* Fallback for unknown type */}
              {!isWalletUser && !isMerchant && (
                <>
                  {(c.display_name || c.merchant_name) && <Row label="Tên" value={c.display_name || c.merchant_name} />}
                  {c.phone && <Row label="Điện thoại" value={c.phone} />}
                  {c.email && <Row label="Email" value={c.email} />}
                </>
              )}
            </div>
          </div>

          {/* ═══ B. VẤN ĐỀ KHÁCH PHẢN ÁNH ═══ */}
          <div className="cht-section">
            <div className="cht-card">
              <h3 className="cht-card-title">
                <span className="cht-icon">💬</span>
                Vấn đề khách phản ánh
              </h3>

              {/* Original complaint */}
              {(problem?.original_complaint || t.customer_problem) && (
                <p className="cht-problem-text">
                  {problem?.original_complaint || t.customer_problem}
                </p>
              )}

              {/* Latest customer message (only if different) */}
              {(problem?.latest_customer_message || t.latest_customer_message) &&
                (problem?.latest_customer_message || t.latest_customer_message) !== (problem?.original_complaint || t.customer_problem) && (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                    Tin nhắn gần nhất
                  </div>
                  <p className="cht-problem-text" style={{ marginBottom: 0 }}>
                    {problem?.latest_customer_message || t.latest_customer_message}
                  </p>
                </div>
              )}

              {/* Extracted structured info */}
              <ExtractedInfoGrid
                extractedInfo={t.extracted_info}
                problem={problem}
                providedInfo={t.customer_provided_info || []}
              />
            </div>
          </div>

          {/* ═══ C. AGENT DIAGNOSIS ═══ */}
          <div className="cht-section">
            <div className="cht-card">
              <h3 className="cht-card-title">
                <span className="cht-icon">🧠</span>
                Chẩn đoán của Agent
              </h3>

              <div className="cht-diagnosis-grid">
                {diagnosis?.what_was_checked && diagnosis.what_was_checked.length > 0 && (
                  <DiagnosisItem
                    label="Đã kiểm tra"
                    value={diagnosis.what_was_checked.join(', ')}
                  />
                )}

                {diagnosis?.confirmed_facts && diagnosis.confirmed_facts.length > 0 && (
                  <DiagnosisItem
                    label="Xác nhận được"
                    value={diagnosis.confirmed_facts.join(', ')}
                  />
                )}

                <DiagnosisItem
                  label="Vị trí vấn đề"
                  value={diagnosis?.likely_bottleneck || 'Chưa xác định đầy đủ — cần kiểm tra thêm'}
                  warning={!diagnosis?.likely_bottleneck || diagnosis.likely_bottleneck.includes('Chưa xác định')}
                />

                <DiagnosisItem
                  label="Độ tin cậy"
                  value={confInfo.label}
                  warning={!!confInfo.cls}
                />

                <DiagnosisItem
                  label="Tại sao cần nhân viên xử lý"
                  value={diagnosis?.why_staff_action_needed || 'Cần kiểm tra thêm'}
                  warning={!diagnosis?.why_staff_action_needed}
                />
              </div>
            </div>
          </div>

          {/* ═══ D. EVIDENCE SUMMARY ═══ */}
          <div className="cht-section">
            <div className="cht-card">
              <h3 className="cht-card-title">
                <span className="cht-icon">📊</span>
                Bằng chứng
              </h3>

              <div className="cht-evidence-grid">
                {checklist.map((item, i) => (
                  <div key={i} className="cht-evidence-item">
                    <span className={`cht-evidence-icon ${item.status}`}>
                      {EV_ICONS[item.status] || '❓'}
                    </span>
                    <span className="cht-evidence-label">{item.label}</span>
                    <span className={`cht-evidence-status ${item.status}`}>
                      {EV_STATUS_LABELS[item.status] || item.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* ═══ E. CONVERSATION TIMELINE ═══ */}
          <div className="cht-section">
            <div className="cht-card">
              <h3 className="cht-card-title">
                <span className="cht-icon">📝</span>
                Diễn biến hội thoại
              </h3>

              {messagesToShow.length === 0 && (
                <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                  Không có dữ liệu hội thoại.
                </span>
              )}

              <div className="cht-timeline">
                {messagesToShow.map((m, i) => (
                  <div key={i} className={`cht-msg ${m.role === 'customer' ? 'cht-msg-customer' : 'cht-msg-agent'}`}>
                    <div className="cht-msg-role">
                      {m.role === 'customer' ? 'Khách hàng' : 'Agent'}
                    </div>
                    <div className="cht-msg-text">{m.text}</div>
                    {m.timestamp && <div className="cht-msg-time">{m.timestamp}</div>}
                  </div>
                ))}
              </div>

              {hasMoreMessages && !showAllMessages && (
                <button className="cht-show-all-btn" onClick={() => setShowAllMessages(true)}>
                  Xem toàn bộ hội thoại ({allMessages.length} tin nhắn)
                </button>
              )}
            </div>
          </div>

          {/* ═══ F. CHI TIẾT KỸ THUẬT (collapsed) ═══ */}
          <div className="cht-section">
            <button className="cht-tech-toggle" onClick={() => setShowTechDetails(!showTechDetails)}>
              <span>🔬 Chi tiết kỹ thuật</span>
              <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', transition: 'transform 0.2s', transform: showTechDetails ? 'rotate(0deg)' : 'rotate(-90deg)' }}>▾</span>
            </button>
            {showTechDetails && (
              <div className="cht-tech-body">
                <pre>{JSON.stringify({
                  ticket_id: t.ticket_id,
                  source: t.source,
                  case_id: t.customer_chat_case_id,
                  workflow: t.selected_workflow,
                  issue_type: t.issue_type,
                  handoff_reason: t.handoff_reason,
                  extracted_info: t.extracted_info,
                  rendered_extracted_fields: {
                    extracted_amount: t.customer_problem_structured?.extracted_amount,
                    extracted_time: t.customer_problem_structured?.extracted_time,
                    extracted_bank_provider: t.customer_problem_structured?.extracted_bank_provider,
                    extracted_transaction_id: t.customer_problem_structured?.extracted_transaction_id,
                  },
                  evidence_summary_present: !!(t.internal_staff_evidence_summary && Object.keys(t.internal_staff_evidence_summary).length),
                  rule_result_present: !!(t.internal_staff_evidence_summary as Record<string, unknown>)?.['rule_diagnosis'],
                  staff_diagnosis: t.agent_diagnosis,
                  staff_action_contract: t.staff_action ? {
                    action_title: t.staff_action.action_title,
                    action_type: t.staff_action.action_type,
                    approval_required: t.staff_action.approval_required,
                    approve_button_label: t.staff_action.approve_button_label,
                    audit_event_type: t.staff_action.audit_event_type,
                  } : null,
                  diagnosis: t.public_safe_diagnosis,
                  evidence: t.internal_staff_evidence_summary,
                  recommended_action: t.recommended_action,
                  linked_draft: t.linked_action_draft_id,
                }, null, 2)}</pre>
              </div>
            )}
          </div>
        </div>

        {/* ─── RIGHT COLUMN (35%) — Sticky Action Panel ─── */}
        <div className="cht-right">
          <div className="cht-action-panel">
            <h3 className="cht-action-title">
              ⚡ Việc nhân viên cần làm
            </h3>

            {/* A. Action title + meta */}
            <div className="cht-action-recommended">
              <div className="cht-action-label">
                {action?.action_title || 'Xử lý ticket'}
              </div>
              <div className="cht-action-meta">
                {action?.next_owner_team && (
                  <span className="badge badge-purple">Team: {action.next_owner_team}</span>
                )}
                <span className={`badge ${t.approval_required ? 'badge-red' : 'badge-green'}`}>
                  {t.approval_required ? 'Cần phê duyệt: Có' : 'Cần phê duyệt: Không'}
                </span>
                <span className="badge badge-neutral">
                  Trạng thái: {statusInfo.label}
                </span>
              </div>
            </div>

            {/* B. Why recommended */}
            {action?.why_recommended && (
              <div className="cht-action-section">
                <div className="cht-action-section-title">Vì sao đề xuất bước này?</div>
                <p style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', lineHeight: 1.5, margin: 0 }}>
                  {action.why_recommended}
                </p>
              </div>
            )}

            {/* C. What approve does */}
            {action && action.approve_effect.length > 0 && (
              <div className="cht-action-section">
                <div className="cht-action-section-title">✅ Khi bấm nút này, hệ thống sẽ:</div>
                <ul className="cht-action-list cht-do">
                  {action.approve_effect.map((e, i) => <li key={i}>{e}</li>)}
                </ul>
              </div>
            )}

            {/* D. What approve does NOT do */}
            {action && action.approve_does_not_do.length > 0 && (
              <div className="cht-action-section">
                <div className="cht-action-section-title">🚫 Hệ thống sẽ KHÔNG:</div>
                <ul className="cht-action-list cht-dont">
                  {action.approve_does_not_do.map((e, i) => <li key={i}>{e}</li>)}
                </ul>
              </div>
            )}

            {/* E. Preconditions checked */}
            {action && action.preconditions_checked.length > 0 && (
              <div className="cht-action-section">
                <div className="cht-action-section-title">🛡️ Điều kiện đã kiểm tra</div>
                <ul className="cht-action-list cht-checklist">
                  {action.preconditions_checked.map((p, i) => <li key={`c${i}`}>{p}</li>)}
                </ul>
              </div>
            )}

            {/* F. Missing preconditions */}
            <div className="cht-action-section">
              <div className="cht-action-section-title">⚠️ Còn thiếu / cần kiểm tra thêm</div>
              {action && action.missing_preconditions.length > 0 ? (
                <ul className="cht-action-list cht-dont">
                  {action.missing_preconditions.map((p, i) => <li key={`m${i}`} className="cht-missing">{p}</li>)}
                </ul>
              ) : (
                <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)', margin: 0 }}>
                  Không có điều kiện thiếu quan trọng.
                </p>
              )}
            </div>

            {/* Safety warnings */}
            {action && action.safety_warnings.length > 0 && (
              <div className="cht-action-section">
                <div className="alert alert-warning" style={{ fontSize: '0.78rem', margin: 0 }}>
                  <span className="alert-icon">⚠️</span>
                  <div>
                    {action.safety_warnings.map((w, i) => <div key={i}>{w}</div>)}
                  </div>
                </div>
              </div>
            )}

            {/* Buttons — label derived from contract + approval_required */}
            <div className="cht-action-buttons">
              <button
                className="btn btn-success"
                disabled={busy}
                onClick={() => setShowApproveModal(true)}
              >
                {(() => {
                  const lbl = action?.approve_button_label || '';
                  if (!t.approval_required && lbl.startsWith('Phê duyệt')) {
                    return 'Xác nhận đã review';
                  }
                  return lbl || (t.approval_required ? 'Phê duyệt bước xử lý' : 'Xác nhận đã review');
                })()}
              </button>
              <button
                className="btn btn-danger"
                disabled={busy}
                onClick={() => decide('reject')}
              >
                Từ chối
              </button>
              <button
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => decide('requestInfo')}
              >
                Yêu cầu bổ sung
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ══════ APPROVE CONFIRMATION MODAL ══════ */}
      {showApproveModal && action && (
        <ApproveConfirmModal
          ticketId={t.ticket_id}
          staffAction={action}
          currentStatus={statusInfo.label}
          busy={busy}
          onConfirm={() => decide('approve')}
          onCancel={() => setShowApproveModal(false)}
        />
      )}
    </div>
  );
}

/* ─── Helper components ─── */

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="cht-row">
      <span className="cht-label">{label}</span>
      <span className="cht-value" style={mono ? { fontFamily: "'JetBrains Mono', monospace", fontSize: '0.82rem' } : undefined}>
        {value}
      </span>
    </div>
  );
}

function DiagnosisItem({ label, value, warning }: { label: string; value: string; warning?: boolean }) {
  return (
    <div className="cht-diagnosis-item">
      <div className="cht-diagnosis-item-label">{label}</div>
      <div className={`cht-diagnosis-item-value ${warning ? 'cht-warning' : ''}`}>{value}</div>
    </div>
  );
}

/* ─── Placeholder label set (must mirror Python _PLACEHOLDER_LABELS) ─── */
const PLACEHOLDER_LABEL_SET = new Set([
  'số tiền giao dịch', 'ngân hàng', 'mã tham chiếu ngân hàng',
  'thời gian giao dịch', 'ngày giao dịch', 'mã giao dịch',
  'mã đơn hàng', 'mã hóa đơn', 'mã khách hàng', 'nhà cung cấp',
  'unknown', 'n/a', 'không rõ', 'transaction id', 'transaction_id',
  'bank name', 'bank_name', 'amount',
]);

/** Return true only when `value` is a real extracted value, not a placeholder label. */
function isMeaningfulExtractedValue(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  const s = String(value).trim().toLowerCase();
  if (!s) return false;
  return !PLACEHOLDER_LABEL_SET.has(s);
}

function ExtractedInfoGrid({ extractedInfo, problem, providedInfo }: {
  extractedInfo?: Record<string, unknown>;
  problem: ChatTicketDetail['customer_problem_structured'];
  providedInfo: string[];
}) {
  const chips: { label: string; value: string }[] = [];

  /* Primary: use actual extracted_info values (from backend real extraction) */
  if (extractedInfo) {
    const amount = extractedInfo['amount'];
    if (isMeaningfulExtractedValue(amount)) {
      const amtNum = Number(amount);
      const amtStr = Number.isFinite(amtNum)
        ? `${amtNum.toLocaleString('vi-VN')}đ`
        : String(amount);
      chips.push({ label: 'Số tiền', value: amtStr });
    }
    const time = extractedInfo['approximate_time_text'] ?? extractedInfo['approximate_date_text'];
    if (isMeaningfulExtractedValue(time)) chips.push({ label: 'Thời gian', value: String(time) });
    const bank = extractedInfo['bank_name'] ?? extractedInfo['provider_name'];
    if (isMeaningfulExtractedValue(bank)) chips.push({ label: 'Ngân hàng/NCC', value: String(bank) });
    const txnId = extractedInfo['transaction_id'];
    if (isMeaningfulExtractedValue(txnId)) chips.push({ label: 'Mã GD', value: String(txnId) });
    const orderId = extractedInfo['order_id'];
    if (isMeaningfulExtractedValue(orderId)) chips.push({ label: 'Mã đơn', value: String(orderId) });
    const billCode = extractedInfo['bill_code'];
    if (isMeaningfulExtractedValue(billCode)) chips.push({ label: 'Mã HĐ', value: String(billCode) });
  }

  /* Secondary: use structured problem fields when extracted_info misses a field */
  if (chips.length === 0 && problem) {
    if (isMeaningfulExtractedValue(problem.extracted_amount))
      chips.push({ label: 'Số tiền', value: problem.extracted_amount });
    if (isMeaningfulExtractedValue(problem.extracted_time))
      chips.push({ label: 'Thời gian', value: problem.extracted_time });
    if (isMeaningfulExtractedValue(problem.extracted_bank_provider))
      chips.push({ label: 'Ngân hàng/NCC', value: problem.extracted_bank_provider });
    if (isMeaningfulExtractedValue(problem.extracted_transaction_id))
      chips.push({ label: 'Mã GD', value: problem.extracted_transaction_id });
    if (isMeaningfulExtractedValue(problem.extracted_order_id))
      chips.push({ label: 'Mã đơn', value: problem.extracted_order_id });
    if (isMeaningfulExtractedValue(problem.extracted_bill_code))
      chips.push({ label: 'Mã HĐ', value: problem.extracted_bill_code });
  }

  /* Last resort: providedInfo labels — skip any that are themselves placeholder labels */
  if (chips.length === 0) {
    providedInfo
      .filter(info => isMeaningfulExtractedValue(info))
      .forEach(info => chips.push({ label: 'Khách cung cấp', value: info }));
  }

  if (chips.length === 0) return null;

  return (
    <div className="cht-extracted-info">
      {chips.map((chip, i) => (
        <div key={i} className="cht-extracted-chip">
          <span className="cht-chip-label">{chip.label}</span>
          <span className="cht-chip-value">{chip.value}</span>
        </div>
      ))}
    </div>
  );
}
