/* ─── Case Detail Page — Redesigned for CS/Ops/Risk staff ─── */
/* Layout: Left = explanation & evidence, Right = sticky approval panel.
   Each concept appears exactly once. No duplicated sections. */

import { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { casesApi } from '../api/cases';
import type { AuditEvent, CaseResponse } from '../api/types';
import { RiskBadge, StatusBadge } from '../components/StatusBadge';
import {
  ClaimVerificationSection,
  AmountVerificationSection,
  LOCATION_LABELS,
  WORKFLOW_FRIENDLY,
  RISK_LABEL,
  MCP_TOOL_FRIENDLY,
  APPROVAL_STATUS_STAFF,
} from '../components/ResolutionTicketPanel';
import { formatCurrency, formatDate } from '../lib/format';
import { useI18n } from '../lib/i18n';

/* ─── Collapsible Accordion (reusable) ─── */
function Accordion({ title, badge, badgeClass, defaultOpen = false, children }: {
  title: string; badge?: string; badgeClass?: string; defaultOpen?: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
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

/* ═══════════════════════════════════════════════════════════════
   MAIN PAGE COMPONENT
   ═══════════════════════════════════════════════════════════════ */

export default function CaseDetailPage() {
  const { t } = useI18n();
  const { caseId } = useParams<{ caseId: string }>();
  const navigate = useNavigate();
  const [caseData, setCaseData] = useState<CaseResponse | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'overview' | 'audit'>('overview');

  /* Approval state */
  const [showReject, setShowReject] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [actionLoading, setActionLoading] = useState(false);
  const [amountVerified, setAmountVerified] = useState(false);

  /* Tech details collapsed state */
  const [showTechDetails, setShowTechDetails] = useState(false);

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

  const ticket = caseData.resolution_ticket;
  const ei = caseData.extracted_info;
  const ev = caseData.evidence;
  const primaryAction = ticket?.recommended_actions?.[0];
  const isWaiting = caseData.status === 'waiting_approval';
  const isMerchantSettlement = caseData.selected_workflow === 'merchant_settlement_delay';

  /* Determine which evidence group is most relevant based on problem location */
  const relevantEvidence = ticket?.problem_location || '';

  /** Translate an evidence key */
  function ek(key: string): string {
    const k = `evidence.${key}`;
    const val = t(k);
    return val !== k ? val : key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  }

  /* Warning banner logic */
  const isRefundAction = caseData.recommended_action === 'create_refund_request_draft';
  const isForceSuccessAction = caseData.recommended_action === 'create_force_success_draft';
  const isUnlockAction = caseData.recommended_action === 'create_unlock_account_draft';
  const isRequestDocsAction = caseData.recommended_action === 'create_request_documents_response_draft';
  const isManualPayoutAction = caseData.recommended_action === 'create_manual_payout_draft';
  const isUncAction = caseData.recommended_action === 'send_unc_email_draft';
  const isBankCorrectionAction = caseData.recommended_action === 'request_bank_account_correction';
  const isIdentityCorrectionAction = caseData.recommended_action === 'request_identity_correction';

  const draftWarning = isRefundAction ? { icon: '💰', k1: 'detail.refund_alert', k2: 'detail.refund_alert2' }
    : isForceSuccessAction ? { icon: '⚡', k1: 'detail.force_success_alert', k2: 'detail.force_success_alert2' }
    : isUnlockAction ? { icon: '🔓', k1: 'detail.unlock_alert', k2: 'detail.unlock_alert2' }
    : isRequestDocsAction ? { icon: '📋', k1: 'detail.request_docs_alert', k2: 'detail.request_docs_alert2' }
    : isManualPayoutAction ? { icon: '🏪', k1: 'detail.manual_payout_alert', k2: 'detail.manual_payout_alert2' }
    : isUncAction ? { icon: '📧', k1: 'detail.unc_alert', k2: 'detail.unc_alert2' }
    : isBankCorrectionAction ? { icon: '🏦', k1: 'detail.bank_correction_alert', k2: 'detail.bank_correction_alert2' }
    : null;

  /* Friendly labels */
  const workflowFriendly = ticket?.ticket_type
    ? (WORKFLOW_FRIENDLY[ticket.ticket_type] || ticket.ticket_type)
    : (caseData.selected_workflow ? t(`workflow.${caseData.selected_workflow}`) : '—');
  const locDisplay = ticket?.problem_location
    ? (LOCATION_LABELS[ticket.problem_location] || { icon: '📍', label: ticket.problem_location })
    : null;
  const riskDisplay = RISK_LABEL[caseData.risk_level || 'unknown'] || RISK_LABEL.unknown;
  const approvalText = APPROVAL_STATUS_STAFF[caseData.approval_status || 'not_required'] || caseData.approval_status || '—';
  const actionFriendly = primaryAction?.mcp_tool
    ? (MCP_TOOL_FRIENDLY[primaryAction.mcp_tool] || primaryAction.action_name)
    : (primaryAction?.action_name || t(`action.${caseData.recommended_action}`));

  /* Consolidated safety notes (deduplicated) */
  const allSafetyNotes = new Set<string>();
  ticket?.safety_notes?.forEach(n => allSafetyNotes.add(n));
  primaryAction?.safety_notes?.forEach(n => allSafetyNotes.add(n));
  const safetyNotes = Array.from(allSafetyNotes);

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

            {/* ═══ 1. PHIẾU XỬ LÝ — Compact Summary Card ═══ */}
            <div className="ci-section">
              <div className="cdp-summary-card">
                <div className="cdp-summary-header">
                  <span className="cdp-summary-icon">🎫</span>
                  <span className="cdp-summary-title">Phiếu xử lý</span>
                  {ticket && (
                    <code className="cdp-ticket-id">{ticket.ticket_id}</code>
                  )}
                </div>
                <div className="cdp-summary-grid">
                  <div className="cdp-summary-item">
                    <span className="cdp-sum-label">Loại dịch vụ</span>
                    <span className="cdp-sum-value">{workflowFriendly}</span>
                  </div>
                  <div className="cdp-summary-item">
                    <span className="cdp-sum-label">Trạng thái</span>
                    <span className="cdp-sum-value"><StatusBadge status={caseData.status} /></span>
                  </div>
                  <div className="cdp-summary-item">
                    <span className="cdp-sum-label">Mức độ rủi ro</span>
                    <span className="cdp-sum-value">{riskDisplay.icon} {riskDisplay.text}</span>
                  </div>
                  <div className="cdp-summary-item">
                    <span className="cdp-sum-label">Phê duyệt</span>
                    <span className="cdp-sum-value">{approvalText}</span>
                  </div>
                  <div className="cdp-summary-item cdp-summary-wide">
                    <span className="cdp-sum-label">Hành động đề xuất</span>
                    <span className="cdp-sum-value cdp-sum-action">{actionFriendly}</span>
                  </div>
                  {ticket?.issue_summary && (
                    <div className="cdp-summary-item cdp-summary-wide">
                      <span className="cdp-sum-label">Tóm tắt</span>
                      <span className="cdp-sum-value">{ticket.issue_summary}</span>
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* ═══ 2. KẾT LUẬN CỦA AGENT — Main Decision Card ═══ */}
            {ticket && (
              <div className="ci-section">
                <div className="cdp-decision-card">
                  <h3 className="cdp-decision-title">🧠 Kết luận của Agent</h3>

                  {/* Vấn đề nằm ở đâu */}
                  {locDisplay && (
                    <div className="cdp-decision-row">
                      <div className="cdp-decision-label">📍 Vấn đề nằm ở đâu</div>
                      <div className="cdp-decision-content">
                        <span className="cdp-location-badge">
                          {locDisplay.icon} {locDisplay.label}
                        </span>
                      </div>
                    </div>
                  )}

                  {/* Vì sao */}
                  {ticket.problem_explanation && (
                    <div className="cdp-decision-row">
                      <div className="cdp-decision-label">🔍 Vì sao</div>
                      <p className="cdp-decision-text">{ticket.problem_explanation}</p>
                    </div>
                  )}

                  {/* Tiền đang ở đâu? — merchant settlement only, near the top */}
                  {isMerchantSettlement && caseData.diagnosis_message && (
                    <div className="cdp-decision-row cdp-decision-highlight">
                      <div className="cdp-decision-label">💰 Tiền đang ở đâu?</div>
                      <p className="cdp-decision-text" style={{ fontWeight: 600 }}>{caseData.diagnosis_message}</p>
                    </div>
                  )}

                  {/* Hành động đề xuất */}
                  {primaryAction && (
                    <div className="cdp-decision-row">
                      <div className="cdp-decision-label">⚡ Hành động đề xuất</div>
                      <p className="cdp-decision-text">
                        <strong>{primaryAction.action_name}</strong>
                        {primaryAction.description && primaryAction.description !== primaryAction.action_name && (
                          <> — {primaryAction.description}</>
                        )}
                      </p>
                      {primaryAction.reason && (
                        <p className="cdp-decision-reason">💡 {primaryAction.reason}</p>
                      )}
                    </div>
                  )}

                  {/* Nhân viên cần làm gì tiếp theo */}
                  {(ticket.staff_instruction || primaryAction?.staff_instruction) && (
                    <div className="cdp-decision-row cdp-decision-highlight">
                      <div className="cdp-decision-label">👷 Nhân viên cần làm gì tiếp theo</div>
                      <p className="cdp-decision-text">
                        {ticket.staff_instruction || primaryAction?.staff_instruction}
                      </p>
                    </div>
                  )}

                  {/* Customer reply draft */}
                  {ticket.customer_reply_draft && (
                    <div className="cdp-decision-row cdp-reply-section">
                      <div className="cdp-reply-header">
                        <span className="cdp-decision-label">💬 Nháp trả lời khách hàng</span>
                        <button
                          className="rt-copy-btn"
                          onClick={async () => {
                            try {
                              await navigator.clipboard.writeText(ticket.customer_reply_draft);
                            } catch { /* clipboard not available */ }
                          }}
                          title="Copy câu trả lời"
                        >📋 Copy</button>
                      </div>
                      <div className="rt-reply-box">{ticket.customer_reply_draft}</div>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* ═══ 3. COMPLAINT ═══ */}
            <div className="ci-section">
              <div className="ci-card">
                <h3 className="ci-card-title">💬 {t('detail.complaint')}</h3>
                <p className="ci-complaint-text">
                  {caseData.raw_complaint || '—'}
                </p>
              </div>
            </div>

            {/* ═══ 4. CLAIM VERIFICATION ═══ */}
            {ticket?.claim_verification && ticket.claim_verification.claims.length > 0 && (
              <div className="ci-section">
                <ClaimVerificationSection cv={ticket.claim_verification} />
              </div>
            )}

            {/* ═══ 4b. AMOUNT VERIFICATION ═══ */}
            {ticket && (
              <div className="ci-section">
                <AmountVerificationSection ticket={ticket} />
              </div>
            )}

            {/* ═══ 5. EVIDENCE — Collapsible Accordions ═══ */}
            {ev && (
              <div className="ci-section">
                <div className="ci-card ci-evidence-card">
                  <h3 className="ci-card-title">📊 Bằng chứng & Dữ liệu hệ thống</h3>

                  {ev.transaction && (
                    <Accordion
                      title="Bằng chứng giao dịch"
                      defaultOpen={relevantEvidence === 'wallet_system' || relevantEvidence === 'bank'}
                    >
                      <EvidenceRows data={ev.transaction} ek={ek} />
                    </Accordion>
                  )}

                  {ev.reconciliation_status && (
                    <Accordion
                      title="Đối soát ngân hàng"
                      badge="Reconciliation"
                      badgeClass="badge-cyan"
                      defaultOpen={relevantEvidence === 'reconciliation'}
                    >
                      <EvidenceRows data={ev.reconciliation_status} ek={ek} exclude={['details']} />
                      {typeof ev.reconciliation_status.details === 'object' && ev.reconciliation_status.details != null && (
                        <EvidenceRows data={ev.reconciliation_status.details as Record<string, unknown>} ek={ek} />
                      )}
                    </Accordion>
                  )}

                  {ev.wallet_ledger && (
                    <Accordion
                      title="Sổ cái ví"
                      badge="Ledger"
                      badgeClass="badge-green"
                      defaultOpen={relevantEvidence === 'wallet_system'}
                    >
                      <EvidenceRows data={ev.wallet_ledger} ek={ek} exclude={['entries']} />
                    </Accordion>
                  )}

                  {ev.fraud_case && (
                    <Accordion
                      title="Kiểm tra gian lận"
                      badge="Fraud"
                      badgeClass="badge-red"
                      defaultOpen={relevantEvidence === 'fraud_system'}
                    >
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
                    </Accordion>
                  )}

                  {ev.provider_status && (
                    <Accordion
                      title="Trạng thái nhà cung cấp"
                      badge="Provider"
                      badgeClass="badge-blue"
                      defaultOpen={relevantEvidence === 'provider'}
                    >
                      <EvidenceRows data={ev.provider_status} ek={ek} />
                      {ev.provider_status.status === 'not_confirmed' && (
                        <div className="alert alert-info mt-2" style={{ fontSize: '0.78rem' }}>
                          <span className="alert-icon">ℹ️</span>
                          <span><strong>{t('detail.evidence_provider_note')}</strong> {t('detail.evidence_provider_note2')}</span>
                        </div>
                      )}
                    </Accordion>
                  )}

                  {ev.refund_status && (
                    <Accordion title="Trạng thái hoàn tiền" badge="Refund" badgeClass="badge-purple">
                      <EvidenceRows data={ev.refund_status} ek={ek} />
                    </Accordion>
                  )}

                  {ev.account_status && (
                    <Accordion
                      title="Trạng thái tài khoản"
                      badge="Account"
                      badgeClass="badge-amber"
                      defaultOpen={relevantEvidence === 'identity_lookup'}
                    >
                      <EvidenceRows data={ev.account_status} ek={ek} exclude={['recent_transactions', 'device_events']} />
                    </Accordion>
                  )}

                  {/* ── Merchant Settlement Evidence Accordions ── */}
                  {ev.merchant_profile && (
                    <Accordion
                      title="Thông tin Merchant"
                      badge="Merchant"
                      badgeClass="badge-blue"
                      defaultOpen={isMerchantSettlement}
                    >
                      <EvidenceRows data={ev.merchant_profile} ek={ek} />
                    </Accordion>
                  )}

                  {ev.merchant_bank_account && (
                    <Accordion
                      title="Cấu hình tài khoản nhận tiền"
                      badge="Tài khoản NH"
                      badgeClass="badge-cyan"
                      defaultOpen={isMerchantSettlement && relevantEvidence === 'merchant_bank_account'}
                    >
                      <EvidenceRows data={ev.merchant_bank_account} ek={ek} />
                    </Accordion>
                  )}

                  {ev.merchant_settlement_ledger && (
                    <Accordion
                      title="Settlement ledger"
                      badge="Nguồn chuẩn"
                      badgeClass="badge-green"
                      defaultOpen={isMerchantSettlement}
                    >
                      <EvidenceRows data={ev.merchant_settlement_ledger} ek={ek} />
                    </Accordion>
                  )}

                  {ev.settlement_batch && (
                    <Accordion
                      title="Lệnh quyết toán (Settlement batch)"
                      badge="Batch"
                      badgeClass="badge-purple"
                    >
                      <EvidenceRows data={ev.settlement_batch} ek={ek} />
                    </Accordion>
                  )}

                  {ev.merchant_payout && (
                    <Accordion
                      title="Lệnh giải ngân (Payout record)"
                      badge="Payout"
                      badgeClass="badge-amber"
                      defaultOpen={isMerchantSettlement}
                    >
                      <EvidenceRows data={ev.merchant_payout} ek={ek} />
                    </Accordion>
                  )}

                  {ev.bank_transfer_receipt && (
                    <Accordion
                      title="Chứng từ chuyển khoản / UNC"
                      badge="UNC"
                      badgeClass="badge-cyan"
                    >
                      <EvidenceRows data={ev.bank_transfer_receipt} ek={ek} />
                    </Accordion>
                  )}
                </div>
              </div>
            )}

            {/* ═══ 6. CHI TIẾT KỸ THUẬT — Collapsed ═══ */}
            <div className="ci-section">
              <div className="ci-card cdp-tech-section">
                <button className="cdp-tech-toggle" onClick={() => setShowTechDetails(!showTechDetails)}>
                  <span>🔬 Chi tiết kỹ thuật</span>
                  <span className={`ev-chevron ${showTechDetails ? 'ev-chevron-open' : ''}`}>▾</span>
                </button>
                {showTechDetails && (
                  <div className="cdp-tech-body">
                    {/* Workflow & Decision */}
                    <div className="cdp-tech-group">
                      <h4 className="cdp-tech-group-title">Quy trình & Quyết định</h4>
                      <div className="detail-row"><span className="label">Workflow</span><span className="value">{t(`workflow.${caseData.selected_workflow}`)}</span></div>
                      <div className="detail-row"><span className="label">Diagnosis</span><span className="value">{caseData.diagnosis_message || caseData.diagnosis || '—'}</span></div>
                      {caseData.diagnosis_message && caseData.diagnosis && (
                        <div className="detail-row"><span className="label ci-muted-label">Mã nội bộ</span><span className="value ci-muted-val mono">{caseData.diagnosis}</span></div>
                      )}
                      <div className="detail-row"><span className="label">Action code</span><span className="value mono">{caseData.recommended_action || '—'}</span></div>
                      <div className="detail-row"><span className="label">Approval required</span><span className="value">{caseData.approval_required ? 'Yes' : 'No'}</span></div>
                    </div>

                    {/* Extracted Information */}
                    {ei && (
                      <div className="cdp-tech-group">
                        <h4 className="cdp-tech-group-title">Thông tin trích xuất</h4>
                        <div className="badge badge-cyan" style={{ fontSize: '0.68rem', marginBottom: 8 }}>
                          {ei.extraction_method || 'unknown'} {ei.confidence != null && `· ${t('detail.extraction_confidence', { pct: (ei.confidence * 100).toFixed(0) })}`}
                        </div>
                        <div className="detail-row"><span className="label">User ID</span><span className="value">{ei.user_id || '—'}</span></div>
                        <div className="detail-row"><span className="label">Transaction ID</span><span className="value mono">{ei.transaction_id || '—'}</span></div>
                        <div className="detail-row"><span className="label">Service type</span><span className="value">{t(`service.${ei.service_type}`)}</span></div>
                        <div className="detail-row"><span className="label">Issue type</span><span className="value">{t(`issue.${ei.issue_type}`)}</span></div>
                        {ei.order_id && <div className="detail-row"><span className="label">Order ID</span><span className="value mono">{ei.order_id}</span></div>}
                        {ei.bill_code && <div className="detail-row"><span className="label">Bill code</span><span className="value mono">{ei.bill_code}</span></div>}
                        {ei.customer_code && <div className="detail-row"><span className="label">Customer code</span><span className="value mono">{ei.customer_code}</span></div>}
                        {ei.amount_claimed != null && (
                          <div className="detail-row">
                            <span className="label">Amount claimed</span>
                            <span className="value" style={{ color: 'var(--amber)' }}>{formatCurrency(ei.amount_claimed)}</span>
                          </div>
                        )}
                        {ei.missing_fields.length > 0 && (
                          <div className="detail-row">
                            <span className="label">Missing fields</span>
                            <span className="value" style={{ color: 'var(--amber)' }}>{ei.missing_fields.join(', ')}</span>
                          </div>
                        )}
                        {/* Merchant settlement extracted fields */}
                        {ei.merchant_id && <div className="detail-row"><span className="label">Merchant ID</span><span className="value mono">{ei.merchant_id}</span></div>}
                        {ei.merchant_name && <div className="detail-row"><span className="label">Merchant name</span><span className="value">{ei.merchant_name}</span></div>}
                        {ei.tax_code && <div className="detail-row"><span className="label">Tax code</span><span className="value mono">{ei.tax_code}</span></div>}
                        {ei.settlement_cycle && <div className="detail-row"><span className="label">Settlement cycle</span><span className="value">{ei.settlement_cycle}</span></div>}
                        {ei.settlement_date && <div className="detail-row"><span className="label">Settlement date</span><span className="value">{ei.settlement_date}</span></div>}
                        {ei.payout_id && <div className="detail-row"><span className="label">Payout ID</span><span className="value mono">{ei.payout_id}</span></div>}
                        {ei.batch_id && <div className="detail-row"><span className="label">Batch ID</span><span className="value mono">{ei.batch_id}</span></div>}
                      </div>
                    )}

                    {/* Evidence checked / missing */}
                    {ticket && (ticket.evidence_checked.length > 0 || ticket.missing_evidence.length > 0) && (
                      <div className="cdp-tech-group">
                        <h4 className="cdp-tech-group-title">Dữ liệu đã kiểm tra</h4>
                        {ticket.evidence_checked.length > 0 && (
                          <div className="rt-evidence-group">
                            <span className="rt-evidence-heading">✅ Đã kiểm tra:</span>
                            <div className="rt-tags">
                              {ticket.evidence_checked.map((e, i) => (
                                <span key={i} className="rt-tag rt-tag-checked">{e}</span>
                              ))}
                            </div>
                          </div>
                        )}
                        {ticket.missing_evidence.length > 0 && (
                          <div className="rt-evidence-group">
                            <span className="rt-evidence-heading">⚠ Còn thiếu:</span>
                            <div className="rt-tags">
                              {ticket.missing_evidence.map((e, i) => (
                                <span key={i} className="rt-tag rt-tag-missing">{e}</span>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    )}

                    {/* AI Analysis Debug */}
                    {caseData.generated_response && (
                      <div className="cdp-tech-group">
                        <h4 className="cdp-tech-group-title">Chi tiết phân tích AI</h4>
                        <div className="detail-row"><span className="label">Internal summary</span><span className="value">{caseData.generated_response.internal_summary}</span></div>
                        <div className="detail-row"><span className="label">Recommended next step</span><span className="value">{caseData.generated_response.recommended_next_step}</span></div>
                        {caseData.generated_response.debug && (
                          <>
                            <div className="detail-row"><span className="label">Generation mode</span><span className="value mono">{caseData.generated_response.debug.generation_mode}</span></div>
                            {caseData.generated_response.debug.model_used && (
                              <div className="detail-row"><span className="label">Model</span><span className="value mono">{caseData.generated_response.debug.model_used}</span></div>
                            )}
                            {caseData.generated_response.debug.fallback_reason && (
                              <div className="detail-row"><span className="label">Fallback reason</span><span className="value">{caseData.generated_response.debug.fallback_reason}</span></div>
                            )}
                          </>
                        )}
                      </div>
                    )}

                    {/* Draft Output */}
                    {caseData.draft_output && (
                      <div className="cdp-tech-group">
                        <h4 className="cdp-tech-group-title">Draft Output</h4>
                        <pre className="ci-draft-pre">
                          {JSON.stringify(caseData.draft_output, null, 2)}
                        </pre>
                      </div>
                    )}

                    {/* Audit Summary */}
                    <div className="cdp-tech-group">
                      <h4 className="cdp-tech-group-title">Audit</h4>
                      <div className="detail-row"><span className="label">Events</span><span className="value">{audit.length}</span></div>
                      <div className="detail-row"><span className="label">Errors</span><span className="value">{caseData.errors.length}</span></div>
                      {audit.length > 0 && (
                        <div className="detail-row"><span className="label">Last event</span><span className="value">{formatDate(audit[audit.length - 1].timestamp)}</span></div>
                      )}
                      <button className="btn btn-ghost ci-audit-link" onClick={() => setActiveTab('audit')}>
                        Xem timeline đầy đủ →
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* ─── RIGHT COLUMN (35%) — Sticky Approval Panel ─── */}
          <div className="ci-right">
            {(isWaiting || caseData.approval_required) && (
              <div className="ci-section">
                <div className="approval-panel">
                  <h3 className="ci-card-title">🔒 Phê duyệt</h3>

                  {/* Action name */}
                  <div className="cdp-approval-action-row">
                    <span className="cdp-approval-action-name">{actionFriendly}</span>
                    <RiskBadge level={caseData.risk_level} />
                  </div>

                  {/* Approval status */}
                  <div className="detail-row">
                    <span className="label">Trạng thái phê duyệt</span>
                    <span className="value">{approvalText}</span>
                  </div>

                  {/* Merchant settlement approval details */}
                  {isMerchantSettlement && isManualPayoutAction && (
                    <div className="cdp-approval-merchant">
                      {ev?.merchant_settlement_ledger && typeof (ev.merchant_settlement_ledger as Record<string, unknown>).net_settlement_amount === 'number' && (
                        <div className="detail-row">
                          <span className="label">Số tiền giải ngân (settlement ledger)</span>
                          <span className="value" style={{ color: 'var(--green)', fontWeight: 600 }}>
                            {formatCurrency((ev.merchant_settlement_ledger as Record<string, unknown>).net_settlement_amount as number)}
                          </span>
                        </div>
                      )}
                      {ev?.merchant_bank_account && (
                        <div className="detail-row">
                          <span className="label">Trạng thái TK ngân hàng</span>
                          <span className="value">{String((ev.merchant_bank_account as Record<string, unknown>).verification_status ?? '—')}</span>
                        </div>
                      )}
                      <div className="detail-row">
                        <span className="label">Rủi ro payout trùng</span>
                        <span className="value" style={{ color: 'var(--amber)' }}>⚠ Kiểm tra trước khi duyệt</span>
                      </div>
                      <div className="alert alert-danger mt-2" style={{ fontSize: '0.78rem' }}>
                        <span className="alert-icon">🚫</span>
                        <span>Không tự động chuyển tiền. Cần phê duyệt.</span>
                      </div>
                    </div>
                  )}
                  {isMerchantSettlement && isBankCorrectionAction && (
                    <div className="cdp-approval-merchant">
                      <div className="alert alert-info mt-2" style={{ fontSize: '0.78rem' }}>
                        <span className="alert-icon">🏦</span>
                        <span>Yêu cầu Merchant cập nhật tài khoản ngân hàng. Không có payout action.</span>
                      </div>
                    </div>
                  )}
                  {isMerchantSettlement && isUncAction && (
                    <div className="cdp-approval-merchant">
                      <div className="alert alert-info mt-2" style={{ fontSize: '0.78rem' }}>
                        <span className="alert-icon">📧</span>
                        <span>Gửi UNC/mã tham chiếu cho Merchant. Không tạo duplicate payout.</span>
                      </div>
                    </div>
                  )}

                  {/* What happens after approval */}
                  {primaryAction?.expected_result && (
                    <div className="cdp-approval-result">
                      <span className="cdp-approval-result-label">✅ Sau khi phê duyệt</span>
                      <p className="cdp-approval-result-text">{primaryAction.expected_result}</p>
                    </div>
                  )}

                  {/* Consolidated safety warnings */}
                  {safetyNotes.length > 0 && (
                    <div className="cdp-approval-safety">
                      <span className="cdp-approval-safety-label">⚠️ Lưu ý an toàn</span>
                      <ul className="cdp-approval-safety-list">
                        {safetyNotes.map((n, i) => <li key={i}>{n}</li>)}
                      </ul>
                    </div>
                  )}

                  {/* Amount mismatch warning */}
                  {isWaiting && ticket?.amount_verification?.has_amount_mismatch && (
                    <div className="amt-approval-block">
                      <div className="amt-mismatch-warning">
                        <span className="amt-warn-icon">⚠️</span>
                        <span>
                          Khách khai {formatCurrency(ticket.amount_verification.customer_claimed_amount ?? 0)},
                          nhưng hệ thống ghi nhận {formatCurrency(ticket.amount_verification.trusted_amount ?? 0)}.
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

                  {/* Approve / Reject buttons */}
                  {isWaiting && (
                    <div className="ci-approval-actions">
                      <button
                        className="btn btn-success"
                        onClick={handleApprove}
                        disabled={
                          actionLoading ||
                          (ticket?.amount_verification?.has_amount_mismatch === true && !amountVerified)
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

            {/* If no approval needed, show appropriate panel */}
            {!isWaiting && !caseData.approval_required && (
              <div className="ci-section">
                {/* ── Identity correction info panel for merchant not found ── */}
                {isMerchantSettlement && isIdentityCorrectionAction ? (
                  <div className="ci-card cdp-identity-panel" id="identity-correction-panel">
                    <h3 className="ci-card-title">🔍 Cần bổ sung thông tin định danh</h3>
                    <div className="alert alert-info mt-2" style={{ fontSize: '0.82rem' }}>
                      <span className="alert-icon">ℹ️</span>
                      <span>
                        Agent chưa tìm thấy merchant trong hệ thống nên không thể kiểm tra settlement/payout.
                        Nhân viên cần yêu cầu merchant cung cấp lại thông tin định danh.
                      </span>
                    </div>

                    {/* What staff needs to request */}
                    <div className="cdp-identity-instruction" style={{ marginTop: 12 }}>
                      <span className="cdp-approval-result-label">👷 Nhân viên cần yêu cầu merchant cung cấp</span>
                      <ul className="cdp-approval-safety-list" style={{ marginTop: 6 }}>
                        <li>Mã merchant (merchant_id)</li>
                        <li>Số điện thoại / email đăng ký merchant</li>
                        <li>Mã số thuế (MST)</li>
                        <li>Payout ID hoặc Batch ID (nếu có)</li>
                      </ul>
                    </div>

                    {/* Safety notes */}
                    <div className="cdp-approval-safety" style={{ marginTop: 12 }}>
                      <span className="cdp-approval-safety-label">⚠️ Lưu ý an toàn</span>
                      <ul className="cdp-approval-safety-list">
                        <li>Không tạo payout</li>
                        <li>Không gửi UNC</li>
                        <li>Không cập nhật tài khoản ngân hàng</li>
                        <li>Không fallback sang merchant khác</li>
                        <li>Cần định danh merchant trước khi kiểm tra settlement</li>
                      </ul>
                    </div>

                    {/* Status */}
                    <div className="detail-row" style={{ marginTop: 12 }}>
                      <span className="label">Case</span>
                      <span className="value"><StatusBadge status={caseData.status} /></span>
                    </div>
                    <div className="detail-row">
                      <span className="label">Phê duyệt</span>
                      <span className="value">✅ Không cần phê duyệt</span>
                    </div>
                  </div>
                ) : (
                  /* ── Default minimal status for other non-approval cases ── */
                  <div className="ci-card">
                    <h3 className="ci-card-title">📋 Trạng thái</h3>
                    <div className="detail-row"><span className="label">Case</span><span className="value"><StatusBadge status={caseData.status} /></span></div>
                    <div className="detail-row"><span className="label">Phê duyệt</span><span className="value">{approvalText}</span></div>
                    {audit.length > 0 && (
                      <button className="btn btn-ghost ci-audit-link" onClick={() => setActiveTab('audit')}>
                        Xem audit timeline →
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}
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
