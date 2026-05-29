/* ─── Case Detail Page (i18n) — evidence, decision, approval, audit ─── */

import { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { casesApi } from '../api/cases';
import type { AuditEvent, CaseResponse } from '../api/types';
import { ActionBadge, ApprovalBadge, ConflictBadge, RiskBadge, StatusBadge } from '../components/StatusBadge';
import { formatCurrency, formatDate } from '../lib/format';
import { useI18n } from '../lib/i18n';

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

  return (
    <div>
      {/* Header */}
      <div className="page-header">
        <div>
          <div className="flex items-center gap-2 mb-2">
            <button className="btn btn-ghost" onClick={() => navigate('/')} style={{ padding: '4px 8px', fontSize: '0.8rem' }}>{t('common.back')}</button>
          </div>
          <h1 style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span>{t('detail.title')}</span>
            <code className="mono text-sm" style={{ color: 'var(--text-accent)' }}>{caseData.case_id}</code>
          </h1>
          <div className="flex gap-2 mt-2" style={{ flexWrap: 'wrap' }}>
            <StatusBadge status={caseData.status} />
            <RiskBadge level={caseData.risk_level} />
            <ActionBadge action={caseData.recommended_action} />
            {caseData.approval_required && <ApprovalBadge required />}
            <ConflictBadge hasConflict={caseData.has_conflict} />
          </div>
        </div>
      </div>

      {/* Conflict Alert */}
      {caseData.has_conflict && (
        <div className="alert alert-danger mb-3">
          <span className="alert-icon">🚨</span>
          <div>
            <strong>{t('detail.conflict_alert')}</strong> {t('detail.conflict_alert2')}
            {caseData.conflicts.map((c, i) => (
              <div key={i} className="text-sm mt-2">• {c.conflict_type}: {c.description}</div>
            ))}
          </div>
        </div>
      )}

      {/* Refund draft warning */}
      {isRefundAction && (
        <div className="alert alert-warning mb-3">
          <span className="alert-icon">💰</span>
          <div><strong>{t('detail.refund_alert')}</strong> {t('detail.refund_alert2')}</div>
        </div>
      )}

      {/* Force Success draft warning */}
      {isForceSuccessAction && (
        <div className="alert alert-warning mb-3">
          <span className="alert-icon">⚡</span>
          <div><strong>{t('detail.force_success_alert')}</strong> {t('detail.force_success_alert2')}</div>
        </div>
      )}

      {/* Unlock account draft warning */}
      {isUnlockAction && (
        <div className="alert alert-warning mb-3">
          <span className="alert-icon">🔓</span>
          <div><strong>{t('detail.unlock_alert')}</strong> {t('detail.unlock_alert2')}</div>
        </div>
      )}

      {/* Request documents draft warning */}
      {isRequestDocsAction && (
        <div className="alert alert-warning mb-3">
          <span className="alert-icon">📋</span>
          <div><strong>{t('detail.request_docs_alert')}</strong> {t('detail.request_docs_alert2')}</div>
        </div>
      )}

      {/* Tabs */}
      <div className="tabs">
        <button className={`tab ${activeTab === 'overview' ? 'active' : ''}`} onClick={() => setActiveTab('overview')}>{t('detail.tab_overview')}</button>
        <button className={`tab ${activeTab === 'audit' ? 'active' : ''}`} onClick={() => setActiveTab('audit')}>
          {t('detail.tab_audit')} ({audit.length})
        </button>
      </div>

      {activeTab === 'overview' && (
        <div className="detail-grid">
          {/* LEFT COLUMN */}
          <div>
            {/* Complaint */}
            <div className="card mb-3">
              <h4 className="mb-2">{t('detail.complaint')}</h4>
              <p className="text-sm text-secondary" style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
                {caseData.raw_complaint || '—'}
              </p>
            </div>

            {/* Extracted Info */}
            {ei && (
              <div className="card mb-3">
                <h4 className="mb-2">{t('detail.extracted')}</h4>
                <div className="badge badge-cyan mb-2" style={{ fontSize: '0.7rem' }}>
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
            )}

            {/* Decision Panel */}
            <div className="card mb-3">
              <h4 className="mb-2">{t('detail.workflow_decision')}</h4>
              <div className="detail-row"><span className="label">{t('detail.workflow')}</span><span className="value">{t(`workflow.${caseData.selected_workflow}`)}</span></div>
              <div className="detail-row"><span className="label">{t('detail.diagnosis')}</span><span className="value">{caseData.diagnosis_message || caseData.diagnosis || '—'}</span></div>
              {caseData.diagnosis_message && caseData.diagnosis && (
                <div className="detail-row"><span className="label" style={{ fontSize: '0.8rem', opacity: 0.6 }}>{t('detail.diagnosis_code') || 'Mã nội bộ'}</span><span className="value" style={{ fontSize: '0.8rem', opacity: 0.6, fontFamily: 'monospace' }}>{caseData.diagnosis}</span></div>
              )}
              <div className="detail-row"><span className="label">{t('detail.action')}</span><span className="value">{t(`action.${caseData.recommended_action}`)}</span></div>
              <div className="detail-row"><span className="label">{t('detail.risk_level')}</span><span className="value">{t(`risk.${caseData.risk_level}`)}</span></div>
              <div className="detail-row"><span className="label">{t('detail.approval_required')}</span><span className="value">{caseData.approval_required ? t('common.yes') : t('common.no')}</span></div>
              <div className="detail-row"><span className="label">{t('detail.approval_status')}</span><span className="value">{t(`approval.${caseData.approval_status}`)}</span></div>
            </div>
          </div>

          {/* RIGHT COLUMN */}
          <div>
            {/* Evidence */}
            {ev && (
              <div className="card mb-3">
                <h4 className="mb-2">{t('detail.evidence')}</h4>

                {/* Transaction */}
                {ev.transaction && (
                  <div className="detail-section">
                    <h4>{t('detail.evidence_transaction')}</h4>
                    {Object.entries(ev.transaction).map(([k, v]) => (
                      <div className="detail-row" key={k}>
                        <span className="label">{ek(k)}</span>
                        <span className="value">{typeof v === 'number' ? formatCurrency(v) : String(v ?? '—')}</span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Wallet Ledger */}
                {ev.wallet_ledger && (
                  <div className="detail-section">
                    <h4>{t('detail.evidence_wallet')} <span className="badge badge-green" style={{ fontSize: '0.65rem' }}>{t('detail.evidence_wallet_badge')}</span></h4>
                    {Object.entries(ev.wallet_ledger).filter(([k]) => k !== 'entries').map(([k, v]) => (
                      <div className="detail-row" key={k}>
                        <span className="label">{ek(k)}</span>
                        <span className="value">{typeof v === 'number' ? formatCurrency(v) : String(v ?? '—')}</span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Provider Status */}
                {ev.provider_status && (
                  <div className="detail-section">
                    <h4>{t('detail.evidence_provider')} <span className="badge badge-blue" style={{ fontSize: '0.65rem' }}>{t('detail.evidence_provider_badge')}</span></h4>
                    {Object.entries(ev.provider_status).map(([k, v]) => (
                      <div className="detail-row" key={k}>
                        <span className="label">{ek(k)}</span>
                        <span className="value">{String(v ?? '—')}</span>
                      </div>
                    ))}
                    {ev.provider_status.status === 'not_confirmed' && (
                      <div className="alert alert-info mt-2" style={{ fontSize: '0.78rem' }}>
                        <span className="alert-icon">ℹ️</span>
                        <span><strong>{t('detail.evidence_provider_note')}</strong> {t('detail.evidence_provider_note2')}</span>
                      </div>
                    )}
                  </div>
                )}

                {/* Refund Status */}
                {ev.refund_status && (
                  <div className="detail-section">
                    <h4>{t('detail.evidence_refund')} <span className="badge badge-purple" style={{ fontSize: '0.65rem' }}>{t('detail.evidence_refund_badge')}</span></h4>
                    {Object.entries(ev.refund_status).map(([k, v]) => (
                      <div className="detail-row" key={k}>
                        <span className="label">{ek(k)}</span>
                        <span className="value">{String(v ?? '—')}</span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Reconciliation Status */}
                {ev.reconciliation_status && (
                  <div className="detail-section">
                    <h4>{t('detail.evidence_reconciliation')} <span className="badge badge-cyan" style={{ fontSize: '0.65rem' }}>{t('detail.evidence_reconciliation_badge')}</span></h4>
                    {Object.entries(ev.reconciliation_status).filter(([k]) => k !== 'details').map(([k, v]) => (
                      <div className="detail-row" key={k}>
                        <span className="label">{ek(k)}</span>
                        <span className="value">{typeof v === 'boolean' ? (v ? '✅ Có' : '❌ Không') : typeof v === 'number' ? formatCurrency(v) : String(v ?? '—')}</span>
                      </div>
                    ))}
                    {/* Render bank details from nested details object */}
                    {typeof ev.reconciliation_status.details === 'object' && ev.reconciliation_status.details != null && Object.entries(ev.reconciliation_status.details as Record<string, unknown>).map(([k, v]) => (
                      <div className="detail-row" key={`details_${k}`}>
                        <span className="label">{ek(k)}</span>
                        <span className="value">{typeof v === 'boolean' ? (v ? '✅ Có' : '❌ Không') : typeof v === 'number' ? formatCurrency(v) : String(v ?? '—')}</span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Account Status (Fraud Use Case) */}
                {ev.account_status && (
                  <div className="detail-section">
                    <h4>{t('detail.evidence_account')} <span className="badge badge-amber" style={{ fontSize: '0.65rem' }}>{t('detail.evidence_account_badge')}</span></h4>
                    {Object.entries(ev.account_status).filter(([k]) => !['recent_transactions', 'device_events'].includes(k)).map(([k, v]) => (
                      <div className="detail-row" key={k}>
                        <span className="label">{ek(k)}</span>
                        <span className="value">{typeof v === 'boolean' ? (v ? '✅' : '❌') : typeof v === 'number' ? formatCurrency(v) : String(v ?? '—')}</span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Fraud Case (Fraud Use Case) */}
                {ev.fraud_case && (
                  <div className="detail-section">
                    <h4>{t('detail.evidence_fraud')} <span className="badge badge-red" style={{ fontSize: '0.65rem' }}>{t('detail.evidence_fraud_badge')}</span></h4>
                    {Object.entries(ev.fraud_case).filter(([k]) => !['signals', 'recent_transactions', 'device_events'].includes(k)).map(([k, v]) => (
                      <div className="detail-row" key={k}>
                        <span className="label">{ek(k)}</span>
                        <span className="value">{typeof v === 'number' ? String(v) : String(v ?? '—')}</span>
                      </div>
                    ))}
                    {/* Signals as sub-section */}
                    {ev.fraud_case.signals && Object.keys(ev.fraud_case.signals).length > 0 && (
                      <div style={{ marginTop: 8 }}>
                        <div className="label" style={{ marginBottom: 4 }}>{ek('signals')}</div>
                        {Object.entries(ev.fraud_case.signals).map(([k, v]) => (
                          <div className="detail-row" key={`sig_${k}`}>
                            <span className="label" style={{ fontSize: '0.8rem', paddingLeft: 12 }}>{k.replace(/_/g, ' ')}</span>
                            <span className="value">{typeof v === 'boolean' ? (v ? '🔴 Yes' : '🟢 No') : String(v ?? '—')}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Approval Panel */}
            {(isWaiting || caseData.approval_required) && (
              <div className="approval-panel mb-3">
                <h4 className="mb-2">{t('detail.approval_panel')}</h4>

                {isRefundAction && (
                  <div className="alert alert-warning mb-2" style={{ fontSize: '0.8rem' }}>
                    <span className="alert-icon">⚠️</span>
                    <span>{t('detail.approval_refund_warning')}</span>
                  </div>
                )}

                {isForceSuccessAction && (
                  <div className="alert alert-warning mb-2" style={{ fontSize: '0.8rem' }}>
                    <span className="alert-icon">⚡</span>
                    <span>{t('detail.approval_force_success_warning')}</span>
                  </div>
                )}

                {isUnlockAction && (
                  <div className="alert alert-warning mb-2" style={{ fontSize: '0.8rem' }}>
                    <span className="alert-icon">🔓</span>
                    <span>{t('detail.approval_unlock_warning')}</span>
                  </div>
                )}

                {isRequestDocsAction && (
                  <div className="alert alert-warning mb-2" style={{ fontSize: '0.8rem' }}>
                    <span className="alert-icon">📋</span>
                    <span>{t('detail.approval_request_docs_warning')}</span>
                  </div>
                )}

                <div className="detail-row"><span className="label">{t('dashboard.col_status')}</span><span className="value"><StatusBadge status={caseData.status} /></span></div>
                <div className="detail-row"><span className="label">{t('detail.action')}</span><span className="value">{t(`action.${caseData.recommended_action}`)}</span></div>
                <div className="detail-row"><span className="label">{t('dashboard.col_risk')}</span><span className="value"><RiskBadge level={caseData.risk_level} /></span></div>

                {isWaiting && (
                  <div className="flex gap-2 mt-3">
                    <button className="btn btn-success" onClick={handleApprove} disabled={actionLoading}>
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
            )}

            {/* Draft Output */}
            {caseData.draft_output && (
              <div className="card mb-3">
                <h4 className="mb-2">{t('detail.draft_output')}</h4>
                <pre className="text-sm font-mono" style={{ background: 'var(--bg-input)', padding: 12, borderRadius: 8, overflow: 'auto', maxHeight: 300, color: 'var(--text-secondary)' }}>
                  {JSON.stringify(caseData.draft_output, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Audit Tab */}
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

      {/* Reject Modal */}
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
