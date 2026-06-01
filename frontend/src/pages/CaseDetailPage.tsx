/* ─── Case Investigation Dashboard — redesigned detail page ─── */

import { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { casesApi } from '../api/cases';
import type { AuditEvent, CaseResponse, TicketAction } from '../api/types';
import { ActionBadge, ApprovalBadge, ConflictBadge, RiskBadge, StatusBadge } from '../components/StatusBadge';
import ResolutionTicketPanel from '../components/ResolutionTicketPanel';
import { formatCurrency, formatDate } from '../lib/format';
import { useI18n } from '../lib/i18n';

/* ─── Collapsible Evidence Accordion ─── */
function EvidenceAccordion({ title, badge, badgeClass, children }: {
  title: string; badge?: string; badgeClass?: string; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className={`ev-accordion ${open ? 'ev-open' : ''}`}>
      <button className="ev-accordion-header" onClick={() => setOpen(!open)}>
        <span className="ev-accordion-title">
          {title}
          {badge && <span className={`badge ${badgeClass || 'badge-blue'}`} style={{ fontSize: '0.6rem', marginLeft: 8 }}>{badge}</span>}
        </span>
        <span className={`ev-chevron ${open ? 'ev-chevron-open' : ''}`}>▾</span>
      </button>
      {open && <div className="ev-accordion-body">{children}</div>}
    </div>
  );
}

/* ─── Evidence Row renderer ─── */
function EvidenceRows({ data, ek, exclude = [] }: {
  data: Record<string, unknown>; ek: (k: string) => string; exclude?: string[];
}) {
  return (
    <>
      {Object.entries(data).filter(([k]) => !exclude.includes(k)).map(([k, v]) => (
        <div className="detail-row" key={k}>
          <span className="label">{ek(k)}</span>
          <span className="value">
            {typeof v === 'boolean' ? (v ? '✅' : '❌')
              : typeof v === 'number' ? formatCurrency(v)
              : String(v ?? '—')}
          </span>
        </div>
      ))}
    </>
  );
}

/* ─── Staff-Friendly Action Detail for Approval Panel ─── */
const EXEC_MODE_STAFF: Record<string, string> = {
  draft_only: 'Chỉ tạo bản nháp, chưa xử lý thật',
  read_only:  'Chỉ tra cứu, không thay đổi dữ liệu',
  manual:     'Nhân viên cần xử lý thủ công',
};
const APPROVAL_STATUS_STAFF: Record<string, string> = {
  not_required: '✅ Không cần phê duyệt',
  pending: '⏳ Đang chờ phê duyệt',
  approved: '✅ Đã được phê duyệt',
  rejected: '❌ Đã bị từ chối',
};
const RISK_LABEL: Record<string, { icon: string; text: string }> = {
  low:      { icon: '🟢', text: 'Thấp' },
  medium:   { icon: '🟡', text: 'Trung bình' },
  high:     { icon: '🟠', text: 'Cao' },
  critical: { icon: '🔴', text: 'Nghiêm trọng' },
  unknown:  { icon: '⚪', text: 'Chưa xác định' },
};
const MCP_TOOL_FRIENDLY: Record<string, string> = {
  create_force_success_draft:    'Tạo yêu cầu cập nhật giao dịch thành công',
  create_refund_draft:           'Tạo yêu cầu hoàn tiền',
  create_reconciliation_draft:   'Tạo phiếu đối soát',
  create_unlock_account_draft:   'Tạo yêu cầu mở khóa tài khoản',
  create_customer_response_draft:'Soạn phản hồi cho khách hàng',
};
const ACTION_STATUS_STAFF: Record<string, { icon: string; label: string; cls: string }> = {
  draft:            { icon: '📝', label: 'Bản nháp',             cls: 'badge-blue' },
  draft_ready:      { icon: '📝', label: 'Nháp sẵn sàng',       cls: 'badge-blue' },
  waiting_approval: { icon: '⏳', label: 'Đang chờ phê duyệt',  cls: 'badge-amber' },
  manual_required:  { icon: '🛠', label: 'Cần xử lý thủ công',  cls: 'badge-purple' },
};

function ActionDetailSection({ action }: { action: TicketAction }) {
  const [showTech, setShowTech] = useState(false);
  const statusDisplay = ACTION_STATUS_STAFF[action.status] || ACTION_STATUS_STAFF.manual_required;
  const risk = RISK_LABEL[action.risk_level] || RISK_LABEL.unknown;
  const toolFriendly = action.mcp_tool ? (MCP_TOOL_FRIENDLY[action.mcp_tool] || action.mcp_tool) : null;

  return (
    <div className="ad-section">
      {/* ── Header ── */}
      <div className="ad-compact">
        <div className="ad-name-row">
          <span className="ad-name">{action.action_name}</span>
          <span className={`badge ${statusDisplay.cls}`} style={{ fontSize: '0.65rem' }}>
            {statusDisplay.icon} {statusDisplay.label}
          </span>
        </div>
      </div>

      {/* ── Staff-Friendly Summary ── */}
      <div className="ad-staff-summary">
        <div className="ad-sf-row">
          <span className="ad-sf-label">📌 Việc cần làm</span>
          <span className="ad-sf-value">{action.description || action.action_name}</span>
        </div>

        {action.reason && (
          <div className="ad-sf-row">
            <span className="ad-sf-label">💡 Vì sao</span>
            <span className="ad-sf-value">{action.reason}</span>
          </div>
        )}

        {toolFriendly && (
          <div className="ad-sf-row">
            <span className="ad-sf-label">🔧 Công cụ hệ thống</span>
            <span className="ad-sf-value">{toolFriendly}</span>
          </div>
        )}

        <div className="ad-sf-row">
          <span className="ad-sf-label">⚙️ Cách thực hiện</span>
          <span className="ad-sf-value">{EXEC_MODE_STAFF[action.execution_mode] || action.execution_mode}</span>
        </div>

        <div className="ad-sf-row">
          <span className="ad-sf-label">🔑 Cần phê duyệt</span>
          <span className="ad-sf-value">
            {action.requires_approval
              ? (APPROVAL_STATUS_STAFF[action.approval_status] || action.approval_status)
              : '✅ Không cần phê duyệt'}
          </span>
        </div>

        <div className="ad-sf-row">
          <span className="ad-sf-label">📊 Mức độ rủi ro</span>
          <span className="ad-sf-value">{risk.icon} {risk.text}</span>
        </div>

        {action.preconditions.length > 0 && (
          <div className="ad-sf-row">
            <span className="ad-sf-label">🔍 Cần kiểm tra</span>
            <ul className="ad-sf-list">
              {action.preconditions.map((p, i) => <li key={i}>{p}</li>)}
            </ul>
          </div>
        )}

        {action.expected_result && (
          <div className="ad-sf-row ad-sf-result">
            <span className="ad-sf-label">✅ Sau khi phê duyệt</span>
            <span className="ad-sf-value">{action.expected_result}</span>
          </div>
        )}

        {action.safety_notes.length > 0 && (
          <div className="ad-sf-row ad-sf-safety">
            <span className="ad-sf-label">⚠️ Lưu ý an toàn</span>
            <ul className="ad-sf-list ad-sf-safety-list">
              {action.safety_notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          </div>
        )}

        {action.staff_instruction && (
          <div className="ad-sf-row ad-sf-instruction">
            <span className="ad-sf-label">👷 Hướng dẫn</span>
            <span className="ad-sf-value">{action.staff_instruction}</span>
          </div>
        )}
      </div>

      {/* ── Technical Details (collapsed) ── */}
      <button className="ad-tech-toggle" onClick={() => setShowTech(!showTech)}>
        <span>🔬 Chi tiết kỹ thuật</span>
        <span className={`sf-chevron ${showTech ? 'sf-chevron-open' : ''}`}>▾</span>
      </button>
      {showTech && (
        <div className="ad-tech-body">
          <div className="ad-tech-row"><span className="ad-tech-label">action_type</span><code>{action.action_type}</code></div>
          <div className="ad-tech-row"><span className="ad-tech-label">action_id</span><code>{action.action_id}</code></div>
          {action.mcp_tool && <div className="ad-tech-row"><span className="ad-tech-label">mcp_tool</span><code>{action.mcp_tool}</code></div>}
          {action.mcp_input && Object.keys(action.mcp_input).length > 0 && (
            <div className="ad-tech-row"><span className="ad-tech-label">mcp_input</span><pre className="ad-tech-pre">{JSON.stringify(action.mcp_input, null, 2)}</pre></div>
          )}
          {action.evidence_dependencies.length > 0 && (
            <div className="ad-tech-row"><span className="ad-tech-label">evidence_dependencies</span><span>{action.evidence_dependencies.join(', ')}</span></div>
          )}
          <div className="ad-tech-row"><span className="ad-tech-label">execution_mode</span><code>{action.execution_mode}</code></div>
          <div className="ad-tech-row"><span className="ad-tech-label">risk_level</span><code>{action.risk_level}</code></div>
          <div className="ad-tech-row"><span className="ad-tech-label">approval_status</span><code>{action.approval_status}</code></div>
          <div className="ad-tech-row"><span className="ad-tech-label">status</span><code>{action.status}</code></div>
        </div>
      )}
    </div>
  );
}

/* ─── Approval Explanation for non-technical staff ─── */
function ApprovalExplanation({ actions }: { actions: TicketAction[] }) {
  if (!actions.length) return null;
  const action = actions[0]; // primary action
  const toolFriendly = action.mcp_tool ? (MCP_TOOL_FRIENDLY[action.mcp_tool] || action.mcp_tool) : action.action_name;
  const safetyLines = action.safety_notes.length > 0
    ? action.safety_notes
    : ['Hệ thống không tự động thực hiện action nào ảnh hưởng tiền hoặc tài khoản.'];

  return (
    <div className="ad-approval-explain">
      <div className="ad-explain-title">ℹ️ Bạn đang phê duyệt điều gì?</div>
      <p className="ad-explain-what">
        Bạn đang phê duyệt việc <strong>{toolFriendly.toLowerCase()}</strong> cho case này.
      </p>
      {action.expected_result && (
        <p className="ad-explain-result">
          <strong>Sau khi duyệt:</strong> {action.expected_result}
        </p>
      )}
      <div className="ad-explain-safety">
        <strong>Hệ thống sẽ KHÔNG tự động:</strong>
        <ul>
          {safetyLines.map((n, i) => <li key={i}>{n}</li>)}
        </ul>
      </div>
    </div>
  );
}

export default function CaseDetailPage() {
  const { t } = useI18n();
  const { caseId } = useParams<{ caseId: string }>();
  const navigate = useNavigate();
  const [caseData, setCaseData] = useState<CaseResponse | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'overview' | 'audit'>('overview');

  /* Approval modal state */
  const [showReject, setShowReject] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [actionLoading, setActionLoading] = useState(false);
  const [amountVerified, setAmountVerified] = useState(false);

  const loadData = useCallback(async () => {
    if (!caseId) return;
    setLoading(true);
    try {
      const [c, a] = await Promise.all([
        casesApi.get(caseId),
        casesApi.getAudit(caseId),
      ]);
      setCaseData(c);
      setAudit(a.events);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('detail.load_error'));
    } finally {
      setLoading(false);
    }
  }, [caseId, t]);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- data fetch on caseId change
  useEffect(() => { void loadData(); }, [loadData]);

  const handleApprove = async () => {
    if (!caseId) return;
    setActionLoading(true);
    try {
      await casesApi.approve(caseId, { approver: 'admin_user', comment: t('detail.approve_comment') });
      await loadData();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('detail.approve_error'));
    } finally {
      setActionLoading(false);
    }
  };

  const handleReject = async () => {
    if (!caseId || !rejectReason.trim()) return;
    setActionLoading(true);
    try {
      await casesApi.reject(caseId, { approver: 'admin_user', reason: rejectReason });
      setShowReject(false);
      await loadData();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('detail.reject_error'));
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) return <div className="loading"><div className="spinner" /></div>;
  if (error) return <div className="alert alert-danger"><span className="alert-icon">⚠</span>{error}</div>;
  if (!caseData) return <div className="alert alert-danger">{t('detail.not_found')}</div>;

  const ei = caseData.extracted_info;
  const ev = caseData.evidence;
  const isRefundAction = caseData.recommended_action === 'create_refund_request_draft';
  const isForceSuccessAction = caseData.recommended_action === 'create_force_success_draft';
  const isUnlockAction = caseData.recommended_action === 'create_unlock_account_draft';
  const isRequestDocsAction = caseData.recommended_action === 'create_request_documents_response_draft';
  const isWaiting = caseData.status === 'waiting_approval';

  /** Translate an evidence key */
  function ek(key: string): string {
    const k = `evidence.${key}`;
    const val = t(k);
    return val !== k ? val : key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  }

  /* Draft action warning text */
  const draftWarning = isRefundAction ? { icon: '💰', k1: 'detail.refund_alert', k2: 'detail.refund_alert2' }
    : isForceSuccessAction ? { icon: '⚡', k1: 'detail.force_success_alert', k2: 'detail.force_success_alert2' }
    : isUnlockAction ? { icon: '🔓', k1: 'detail.unlock_alert', k2: 'detail.unlock_alert2' }
    : isRequestDocsAction ? { icon: '📋', k1: 'detail.request_docs_alert', k2: 'detail.request_docs_alert2' }
    : null;

  /* Approval panel warning */
  const approvalWarningKey = isRefundAction ? 'detail.approval_refund_warning'
    : isForceSuccessAction ? 'detail.approval_force_success_warning'
    : isUnlockAction ? 'detail.approval_unlock_warning'
    : isRequestDocsAction ? 'detail.approval_request_docs_warning'
    : null;

  return (
    <div className="case-investigation">
      {/* ══════ Compact Header ══════ */}
      <div className="ci-header">
        <div className="ci-header-top">
          <button className="btn btn-ghost ci-back-btn" onClick={() => navigate('/')}>← {t('common.back')}</button>
          <div className="ci-header-id">
            <h1>{t('detail.title')}</h1>
            <code className="ci-case-id">{caseData.case_id}</code>
          </div>
        </div>
        <div className="ci-badges">
          <StatusBadge status={caseData.status} />
          <RiskBadge level={caseData.risk_level} />
          <ActionBadge action={caseData.recommended_action} />
          {caseData.approval_required && <ApprovalBadge required />}
          <ConflictBadge hasConflict={caseData.has_conflict} />
        </div>
      </div>

      {/* ══════ Warning Banners ══════ */}
      {caseData.has_conflict && (
        <div className="alert alert-danger ci-alert">
          <span className="alert-icon">🚨</span>
          <div>
            <strong>{t('detail.conflict_alert')}</strong> {t('detail.conflict_alert2')}
            {caseData.conflicts.map((c, i) => (
              <div key={i} className="text-sm mt-2">• {c.conflict_type}: {c.description}</div>
            ))}
          </div>
        </div>
      )}
      {draftWarning && (
        <div className="alert alert-warning ci-alert">
          <span className="alert-icon">{draftWarning.icon}</span>
          <div><strong>{t(draftWarning.k1)}</strong> {t(draftWarning.k2)}</div>
        </div>
      )}

      {/* ══════ Tabs ══════ */}
      <div className="tabs ci-tabs">
        <button className={`tab ${activeTab === 'overview' ? 'active' : ''}`} onClick={() => setActiveTab('overview')}>{t('detail.tab_overview')}</button>
        <button className={`tab ${activeTab === 'audit' ? 'active' : ''}`} onClick={() => setActiveTab('audit')}>
          {t('detail.tab_audit')} ({audit.length})
        </button>
      </div>

      {/* ══════════════════════════════════════
           OVERVIEW TAB — 2-column layout
         ══════════════════════════════════════ */}
      {activeTab === 'overview' && (
        <div className="ci-grid">
          {/* ─── LEFT COLUMN (65%) ─── */}
          <div className="ci-left">
            {/* 1. Resolution Ticket — top, prominent */}
            <div className="ci-section ci-ai-section">
              <ResolutionTicketPanel
                ticket={caseData.resolution_ticket}
                aiResponse={caseData.generated_response}
              />
            </div>

            {/* 2. Complaint Content */}
            <div className="ci-section">
              <div className="ci-card">
                <h3 className="ci-card-title">💬 {t('detail.complaint')}</h3>
                <p className="ci-complaint-text">
                  {caseData.raw_complaint || '—'}
                </p>
              </div>
            </div>

            {/* 3. Workflow & Decision */}
            <div className="ci-section">
              <div className="ci-card">
                <h3 className="ci-card-title">⚙️ {t('detail.workflow_decision')}</h3>
                <div className="ci-decision-grid">
                  <div className="detail-row"><span className="label">{t('detail.workflow')}</span><span className="value">{t(`workflow.${caseData.selected_workflow}`)}</span></div>
                  <div className="detail-row"><span className="label">{t('detail.diagnosis')}</span><span className="value">{caseData.diagnosis_message || caseData.diagnosis || '—'}</span></div>
                  {caseData.diagnosis_message && caseData.diagnosis && (
                    <div className="detail-row"><span className="label ci-muted-label">{t('detail.diagnosis_code') || 'Mã nội bộ'}</span><span className="value ci-muted-val mono">{caseData.diagnosis}</span></div>
                  )}
                  <div className="detail-row"><span className="label">{t('detail.action')}</span><span className="value">{t(`action.${caseData.recommended_action}`)}</span></div>
                  <div className="detail-row"><span className="label">{t('detail.risk_level')}</span><span className="value">{t(`risk.${caseData.risk_level}`)}</span></div>
                  <div className="detail-row"><span className="label">{t('detail.approval_required')}</span><span className="value">{caseData.approval_required ? t('common.yes') : t('common.no')}</span></div>
                  <div className="detail-row"><span className="label">{t('detail.approval_status')}</span><span className="value">{t(`approval.${caseData.approval_status}`)}</span></div>
                </div>
              </div>
            </div>

            {/* 4. Extracted Information */}
            {ei && (
              <div className="ci-section">
                <div className="ci-card">
                  <h3 className="ci-card-title">🔍 {t('detail.extracted')}</h3>
                  <div className="badge badge-cyan ci-extract-badge">
                    {ei.extraction_method || 'unknown'} {ei.confidence != null && `· ${t('detail.extraction_confidence', { pct: (ei.confidence * 100).toFixed(0) })}`}
                  </div>
                  <div className="detail-row"><span className="label">{t('detail.user_id')}</span><span className="value">{ei.user_id || '—'}</span></div>
                  <div className="detail-row"><span className="label">{t('detail.txn_id')}</span><span className="value mono">{ei.transaction_id || '—'}</span></div>
                  <div className="detail-row"><span className="label">{t('detail.service_type')}</span><span className="value">{t(`service.${ei.service_type}`)}</span></div>
                  <div className="detail-row"><span className="label">{t('detail.issue_type')}</span><span className="value">{t(`issue.${ei.issue_type}`)}</span></div>
                  {ei.order_id && <div className="detail-row"><span className="label">{t('detail.order_id')}</span><span className="value mono">{ei.order_id}</span></div>}
                  {ei.bill_code && <div className="detail-row"><span className="label">{t('detail.bill_code')}</span><span className="value mono">{ei.bill_code}</span></div>}
                  {ei.customer_code && <div className="detail-row"><span className="label">{t('detail.customer_code')}</span><span className="value mono">{ei.customer_code}</span></div>}
                  {ei.amount_claimed != null && (
                    <div className="detail-row">
                      <span className="label">{t('detail.amount_claimed')}</span>
                      <span className="value" style={{ color: 'var(--amber)' }}>
                        {formatCurrency(ei.amount_claimed)}
                        <div className="text-xs text-muted" style={{ fontWeight: 400 }}>{t('detail.amount_claimed_note')}</div>
                      </span>
                    </div>
                  )}
                  {ei.missing_fields.length > 0 && (
                    <div className="detail-row">
                      <span className="label">{t('detail.missing_fields')}</span>
                      <span className="value" style={{ color: 'var(--amber)' }}>{ei.missing_fields.join(', ')}</span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Draft Output (if present) */}
            {caseData.draft_output && (
              <div className="ci-section">
                <div className="ci-card">
                  <h3 className="ci-card-title">📄 {t('detail.draft_output')}</h3>
                  <pre className="ci-draft-pre">
                    {JSON.stringify(caseData.draft_output, null, 2)}
                  </pre>
                </div>
              </div>
            )}
          </div>

          {/* ─── RIGHT COLUMN (35%) — sticky ─── */}
          <div className="ci-right">
            {/* 1. Approval Panel */}
            {(isWaiting || caseData.approval_required) && (
              <div className="ci-section">
                <div className="approval-panel">
                  <h3 className="ci-card-title">🔒 {t('detail.approval_panel')}</h3>

                  {approvalWarningKey && (
                    <div className="alert alert-warning ci-approval-warn">
                      <span className="alert-icon">⚠️</span>
                      <span>{t(approvalWarningKey)}</span>
                    </div>
                  )}

                  <div className="detail-row"><span className="label">{t('dashboard.col_status')}</span><span className="value"><StatusBadge status={caseData.status} /></span></div>
                  <div className="detail-row"><span className="label">{t('dashboard.col_risk')}</span><span className="value"><RiskBadge level={caseData.risk_level} /></span></div>

                  {/* ── Detailed Action Info from Resolution Ticket ── */}
                  {caseData.resolution_ticket?.recommended_actions?.map((action, idx) => (
                    <ActionDetailSection key={idx} action={action} />
                  ))}
                  {/* Fallback if no resolution_ticket */}
                  {(!caseData.resolution_ticket?.recommended_actions?.length) && (
                    <div className="detail-row"><span className="label">{t('detail.action')}</span><span className="value">{t(`action.${caseData.recommended_action}`)}</span></div>
                  )}

                  {/* ── Approval Explanation ── */}
                  {isWaiting && caseData.resolution_ticket?.recommended_actions?.length && (
                    <ApprovalExplanation actions={caseData.resolution_ticket.recommended_actions} />
                  )}

                  {isWaiting && caseData.resolution_ticket?.amount_verification?.has_amount_mismatch && (
                    <div className="amt-approval-block">
                      <div className="amt-mismatch-warning">
                        <span className="amt-warn-icon">⚠️</span>
                        <span>
                          Khách khai {formatCurrency(caseData.resolution_ticket.amount_verification.customer_claimed_amount ?? 0)},
                          nhưng hệ thống ghi nhận {formatCurrency(caseData.resolution_ticket.amount_verification.trusted_amount ?? 0)}.
                          Agent sẽ không dùng số tiền khách khai để tạo yêu cầu.
                          Nhân viên cần xác minh chênh lệch trước khi phê duyệt.
                        </span>
                      </div>
                      <label className="amt-confirm-checkbox">
                        <input
                          type="checkbox"
                          checked={amountVerified}
                          onChange={(e) => setAmountVerified(e.target.checked)}
                        />
                        <span>Tôi đã xác minh chênh lệch số tiền</span>
                      </label>
                    </div>
                  )}

                  {isWaiting && (
                    <div className="ci-approval-actions">
                      <button
                        className="btn btn-success"
                        onClick={handleApprove}
                        disabled={
                          actionLoading ||
                          (caseData.resolution_ticket?.amount_verification?.has_amount_mismatch === true && !amountVerified)
                        }
                      >
                        {actionLoading ? '...' : t('detail.btn_approve')}
                      </button>
                      <button className="btn btn-danger" onClick={() => setShowReject(true)} disabled={actionLoading}>
                        {t('detail.btn_reject')}
                      </button>
                    </div>
                  )}

                  {caseData.approval_status === 'approved' && (
                    <div className="alert alert-success mt-2"><span className="alert-icon">✅</span>{t('detail.approved_msg')}</div>
                  )}
                  {caseData.approval_status === 'rejected' && (
                    <div className="alert alert-danger mt-2"><span className="alert-icon">❌</span>{t('detail.rejected_msg')}</div>
                  )}
                </div>
              </div>
            )}

            {/* 2. Evidence — collapsible accordions */}
            {ev && (
              <div className="ci-section">
                <div className="ci-card ci-evidence-card">
                  <h3 className="ci-card-title">📊 {t('detail.evidence')}</h3>

                  {ev.transaction && (
                    <EvidenceAccordion title={t('detail.evidence_transaction')}>
                      <EvidenceRows data={ev.transaction} ek={ek} />
                    </EvidenceAccordion>
                  )}

                  {ev.wallet_ledger && (
                    <EvidenceAccordion title={t('detail.evidence_wallet')} badge={t('detail.evidence_wallet_badge')} badgeClass="badge-green">
                      <EvidenceRows data={ev.wallet_ledger} ek={ek} exclude={['entries']} />
                    </EvidenceAccordion>
                  )}

                  {ev.provider_status && (
                    <EvidenceAccordion title={t('detail.evidence_provider')} badge={t('detail.evidence_provider_badge')} badgeClass="badge-blue">
                      <EvidenceRows data={ev.provider_status} ek={ek} />
                      {ev.provider_status.status === 'not_confirmed' && (
                        <div className="alert alert-info mt-2" style={{ fontSize: '0.78rem' }}>
                          <span className="alert-icon">ℹ️</span>
                          <span><strong>{t('detail.evidence_provider_note')}</strong> {t('detail.evidence_provider_note2')}</span>
                        </div>
                      )}
                    </EvidenceAccordion>
                  )}

                  {ev.refund_status && (
                    <EvidenceAccordion title={t('detail.evidence_refund')} badge={t('detail.evidence_refund_badge')} badgeClass="badge-purple">
                      <EvidenceRows data={ev.refund_status} ek={ek} />
                    </EvidenceAccordion>
                  )}

                  {ev.reconciliation_status && (
                    <EvidenceAccordion title={t('detail.evidence_reconciliation')} badge={t('detail.evidence_reconciliation_badge')} badgeClass="badge-cyan">
                      <EvidenceRows data={ev.reconciliation_status} ek={ek} exclude={['details']} />
                      {typeof ev.reconciliation_status.details === 'object' && ev.reconciliation_status.details != null && (
                        <EvidenceRows data={ev.reconciliation_status.details as Record<string, unknown>} ek={ek} />
                      )}
                    </EvidenceAccordion>
                  )}

                  {ev.account_status && (
                    <EvidenceAccordion title={t('detail.evidence_account')} badge={t('detail.evidence_account_badge')} badgeClass="badge-amber">
                      <EvidenceRows data={ev.account_status} ek={ek} exclude={['recent_transactions', 'device_events']} />
                    </EvidenceAccordion>
                  )}

                  {ev.fraud_case && (
                    <EvidenceAccordion title={t('detail.evidence_fraud')} badge={t('detail.evidence_fraud_badge')} badgeClass="badge-red">
                      <EvidenceRows data={ev.fraud_case} ek={ek} exclude={['signals', 'recent_transactions', 'device_events']} />
                      {ev.fraud_case.signals && Object.keys(ev.fraud_case.signals).length > 0 && (
                        <div className="ci-signals">
                          <div className="label ci-signals-label">{ek('signals')}</div>
                          {Object.entries(ev.fraud_case.signals).map(([k, v]) => (
                            <div className="detail-row" key={`sig_${k}`}>
                              <span className="label" style={{ paddingLeft: 8, fontSize: '0.8rem' }}>{k.replace(/_/g, ' ')}</span>
                              <span className="value">{typeof v === 'boolean' ? (v ? '🔴 Yes' : '🟢 No') : String(v ?? '—')}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </EvidenceAccordion>
                  )}
                </div>
              </div>
            )}

            {/* 3. Safety Boundaries */}
            <div className="ci-section">
              <div className="ci-card ci-safety-card">
                <h3 className="ci-card-title">🛡️ Safety</h3>
                <ul className="ci-safety-list">
                  <li>Không tự động thực hiện refund/force-success</li>
                  <li>Draft action cần phê duyệt trước khi xử lý</li>
                  <li>LLM chỉ tổng hợp — không quyết định action</li>
                  <li>Không sửa ledger/số dư tự động</li>
                </ul>
              </div>
            </div>

            {/* 4. Quick Audit Summary */}
            <div className="ci-section">
              <div className="ci-card">
                <h3 className="ci-card-title">📜 Audit</h3>
                <div className="detail-row"><span className="label">Events</span><span className="value">{audit.length}</span></div>
                <div className="detail-row"><span className="label">Errors</span><span className="value">{caseData.errors.length}</span></div>
                {audit.length > 0 && (
                  <div className="detail-row"><span className="label">Last</span><span className="value ci-audit-last">{formatDate(audit[audit.length - 1].timestamp)}</span></div>
                )}
                <button className="btn btn-ghost ci-audit-link" onClick={() => setActiveTab('audit')}>
                  View full timeline →
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════
           AUDIT TAB — timeline
         ══════════════════════════════════════ */}
      {activeTab === 'audit' && (
        <div className="card">
          <h4 className="mb-3">{t('detail.audit_title')}</h4>
          {audit.length === 0 ? (
            <p className="text-muted text-sm">{t('detail.audit_empty')}</p>
          ) : (
            <div className="timeline">
              {audit.map((ev) => {
                const cls = getTimelineClass(ev.event_type);
                return (
                  <div key={ev.event_id} className={`timeline-item ${cls}`}>
                    <div className="timeline-header">
                      <span className="timeline-event">{t(`audit.${ev.event_type}`)}</span>
                      <span className="timeline-time">{formatDate(ev.timestamp)}</span>
                    </div>
                    <div className="timeline-actor">{t('detail.audit_by')} {ev.actor}</div>
                    {ev.previous_status && ev.new_status && (
                      <div className="timeline-detail">{ev.previous_status} → {ev.new_status}</div>
                    )}
                    {Object.keys(ev.details).length > 0 && (
                      <details className="mt-2">
                        <summary className="text-xs text-muted" style={{ cursor: 'pointer' }}>{t('common.details')}</summary>
                        <pre className="text-xs font-mono mt-2" style={{ color: 'var(--text-muted)', maxHeight: 150, overflow: 'auto' }}>
                          {JSON.stringify(ev.details, null, 2)}
                        </pre>
                      </details>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ══════ Reject Modal ══════ */}
      {showReject && (
        <div className="modal-overlay" onClick={() => setShowReject(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>{t('detail.reject_modal_title')}</h2>
            <div className="form-group">
              <label className="form-label">{t('detail.reject_reason')}</label>
              <textarea className="form-textarea" value={rejectReason} onChange={e => setRejectReason(e.target.value)} rows={3} placeholder={t('detail.reject_placeholder')} />
            </div>
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setShowReject(false)}>{t('common.cancel')}</button>
              <button className="btn btn-danger" onClick={handleReject} disabled={!rejectReason.trim() || actionLoading}>
                {actionLoading ? '...' : t('detail.reject_btn')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function getTimelineClass(eventType: string): string {
  const danger = ['conflict_detected', 'safety_blocked', 'tool_failed', 'llm_extraction_failed', 'human_rejected', 'dead_letter_created'];
  const highlight = ['approval_requested', 'action_recommended', 'action_proposed', 'rule_applied'];
  const success = ['human_approved', 'draft_created', 'case_closed'];
  if (danger.includes(eventType)) return 'danger';
  if (highlight.includes(eventType)) return 'highlight';
  if (success.includes(eventType)) return 'success';
  return '';
}
