/* ─── Agent Intake Console ─── */
/* Chat-style intake: user message → agent result → input at bottom */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { casesApi } from '../api/cases';
import type { CaseResponse } from '../api/types';
import { ActionBadge, RiskBadge, StatusBadge, ConflictBadge } from '../components/StatusBadge';
import { formatCurrency } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { casesToHistoryItems, getServiceIcon, type CaseHistoryItem } from '../lib/historyStore';

/* ─── Message types for chat history ─── */
type ChatMessage =
  | { role: 'user'; text: string }
  | { role: 'assistant_loading' }
  | { role: 'assistant_result'; data: CaseResponse };

/* Quick-fill chip data */
const CHIP_COMPLAINTS: Record<string, string> = {
  chip_train: 'Tôi đã thanh toán mua vé tàu mã giao dịch TXN_TRAIN_001 nhưng chưa nhận được vé. Số tiền 450,000 VND đã bị trừ. Mong được hỗ trợ hoàn tiền.',
  chip_bill: 'Tôi đã thanh toán tiền điện TXN_BILL_002 nhưng nhà cung cấp chưa xác nhận thanh toán. Mong hỗ trợ.',
  chip_provider: 'Tôi thanh toán tiền nước TXN_BILL_003 nhưng bị lỗi. Tiền đã bị trừ 310,000 VND nhưng hóa đơn chưa được thanh toán.',
  chip_conflict: 'Giao dịch TXN_CONFLICT_001 mua vé tàu bị lỗi. Ví đã trừ tiền nhưng hệ thống hiện đang pending.',
};

export default function CreateCasePage() {
  const { t } = useI18n();
  const navigate = useNavigate();

  /* Form state */
  const [draft, setDraft] = useState('');
  const [userId, setUserId] = useState('');
  const [txnId, setTxnId] = useState('');
  const [serviceType, setServiceType] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  /* Auto-resize textarea */
  const resizeTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const MIN = 52, MAX = 200;
    const next = Math.min(Math.max(el.scrollHeight, MIN), MAX);
    el.style.height = `${next}px`;
    el.style.overflowY = el.scrollHeight > MAX ? 'auto' : 'hidden';
  }, []);

  const handleDraftChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setDraft(e.target.value);
    resizeTextarea();
  };

  /* Chat history */
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState<string | null>(null);

  /* ── History panel state ── */
  const [historyItems, setHistoryItems] = useState<CaseHistoryItem[]>([]);
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null);
  const isViewingHistory = selectedHistoryId !== null;

  const refreshHistory = useCallback(async () => {
    try {
      const list = await casesApi.list();
      setHistoryItems(casesToHistoryItems(list.cases));
    } catch {
      /* Silently fail — history is non-critical */
    }
  }, []);

  /* Load history on mount */
  useEffect(() => {
    let cancelled = false;
    casesApi.list().then(list => {
      if (!cancelled) setHistoryItems(casesToHistoryItems(list.cases));
    }).catch(() => { /* non-critical */ });
    return () => { cancelled = true; };
  }, []);

  const isLoading = messages.length > 0 && messages[messages.length - 1].role === 'assistant_loading';
  const lastResult = messages.length > 0 && messages[messages.length - 1].role === 'assistant_result'
    ? (messages[messages.length - 1] as { role: 'assistant_result'; data: CaseResponse }).data
    : null;

  /* ── Load a history item into the chat (read-only) ── */
  const loadHistoryItem = async (caseId: string) => {
    if (isLoading) return;
    setSelectedHistoryId(caseId);
    setDraft('');
    setError(null);

    try {
      const caseData = await casesApi.get(caseId);
      const msgs: ChatMessage[] = [];
      if (caseData.raw_complaint) {
        msgs.push({ role: 'user', text: caseData.raw_complaint });
      }
      msgs.push({ role: 'assistant_result', data: caseData });
      setMessages(msgs);
    } catch {
      setMessages([]);
      setError('Failed to load case');
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.trim() || isLoading) return;

    /* Exit history viewing mode */
    setSelectedHistoryId(null);

    const complaintText = draft.trim();

    /* 1. Append user message + loading indicator */
    setMessages(prev => [
      ...prev,
      { role: 'user', text: complaintText },
      { role: 'assistant_loading' },
    ]);
    setDraft('');
    setError(null);

    /* Reset textarea height */
    if (textareaRef.current) {
      textareaRef.current.style.height = '52px';
      textareaRef.current.style.overflowY = 'hidden';
    }

    /* 2. Call API */
    try {
      const res = await casesApi.create({
        raw_complaint: complaintText,
        user_id: userId || undefined,
        transaction_id: txnId || undefined,
        service_type: serviceType || undefined,
      });

      /* 3. Replace loading with result */
      setMessages(prev => [
        ...prev.slice(0, -1), // remove loading
        { role: 'assistant_result', data: res },
      ]);

      /* 4. Refresh history list */
      refreshHistory();
    } catch (e: unknown) {
      /* Remove loading on error */
      setMessages(prev => prev.slice(0, -1));
      setError(e instanceof Error ? e.message : t('create.error'));
    }
  };

  const handleNewCase = () => {
    setDraft('');
    setUserId('');
    setTxnId('');
    setServiceType('');
    setMessages([]);
    setError(null);
    setSelectedHistoryId(null);
    /* Reset textarea height & focus */
    requestAnimationFrame(() => {
      if (textareaRef.current) {
        textareaRef.current.style.height = '52px';
        textareaRef.current.style.overflowY = 'hidden';
        textareaRef.current.focus();
      }
    });
  };

  return (
    <div className="intake-layout">
      {/* ════════════ HISTORY PANEL (left) ════════════ */}
      <div className="history-panel">
        <div className="history-header">
          <h3>{t('history.title')}</h3>
          <button className="history-new-btn" onClick={handleNewCase}>{t('history.new_case')}</button>
        </div>
        {historyItems.length === 0 ? (
          <div className="history-empty">
            <div className="history-empty-icon">📋</div>
            <div className="history-empty-text">{t('history.empty')}</div>
            <div className="history-empty-hint">{t('history.empty_hint')}</div>
          </div>
        ) : (
          <div className="history-list">
            {historyItems.map(item => (
              <div
                key={item.id}
                className={`history-item ${selectedHistoryId === item.id ? 'active' : ''}`}
                onClick={() => loadHistoryItem(item.caseId)}
              >
                <div className="history-icon">{getServiceIcon(item.serviceType, item.selectedWorkflow)}</div>
                <div className="history-info">
                  <div className="history-title">{item.title}</div>
                  <div className="history-meta">
                    <span className="history-case-id">{item.caseId}</span>
                    <span className="history-time">{t('history.just_now')}</span>
                  </div>
                  <div className="history-badges">
                    <StatusBadge status={item.status} />
                    {item.riskLevel && <RiskBadge level={item.riskLevel} />}
                    {item.hasConflict && <ConflictBadge hasConflict />}
                    {item.errorCount > 0 && (
                      <span className="badge badge-amber">{t('history.errors', { count: item.errorCount })}</span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ════════════ CENTER COLUMN: Agent Conversation ════════════ */}
      <div className="intake-left">
        {/* Header */}
        <div className="intake-header">
          <h1>🤖 {t('create.title')}</h1>
          <p className="page-subtitle">{t('create.subtitle')}</p>
        </div>

        {/* Chat card */}
        <div className="intake-chat-card">
          {/* Agent identity bar */}
          <div className="agent-identity">
            <div className="agent-avatar">✦</div>
            <div>
              <div className="agent-name">{t('create.agent_name')}</div>
              <div className="agent-status-text">{t('create.agent_status')}</div>
            </div>
          </div>

          {/* Chat body — correct order: intro → user → result → input */}
          <div className="chat-body">
            {/* Agent intro bubble (always first) */}
            <div className="agent-bubble">
              <div className="bubble-avatar">✦</div>
              <div className="bubble-content">{t('create.agent_intro')}</div>
            </div>

            {/* Message history — renders in correct order */}
            {messages.map((msg, i) => {
              if (msg.role === 'user') {
                return (
                  <div key={i} className="user-bubble">
                    <div className="bubble-content">{msg.text}</div>
                  </div>
                );
              }

              if (msg.role === 'assistant_loading') {
                return (
                  <div key={i} className="agent-bubble">
                    <div className="bubble-avatar loading-avatar">✦</div>
                    <div className="bubble-content loading-bubble">
                      <div className="loading-dots">
                        <span /><span /><span />
                      </div>
                      <span className="loading-text">{t('create.submitting')}</span>
                    </div>
                  </div>
                );
              }

              if (msg.role === 'assistant_result') {
                return <ResultCard key={i} result={msg.data} t={t} navigate={navigate} onNewCase={handleNewCase} />;
              }

              return null;
            })}

            {/* Error display */}
            {error && (
              <div className="alert alert-danger" style={{ margin: '12px 0 0' }}>
                <span className="alert-icon">⚠</span>{error}
              </div>
            )}
          </div>

          {/* Chat input — or viewing-history overlay */}
          {isViewingHistory ? (
            <div className="chat-viewing-overlay">
              <span className="viewing-icon">📖</span>
              <div className="viewing-text">
                <strong>{t('history.viewing')}</strong>
                {t('history.viewing_hint')}
              </div>
              <button className="btn btn-primary" onClick={handleNewCase} style={{ flexShrink: 0 }}>
                {t('history.new_case')}
              </button>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="chat-input-area">
              <textarea
                ref={textareaRef}
                className="chat-textarea"
                value={draft}
                onChange={handleDraftChange}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    if (draft.trim() && !isLoading) {
                      handleSubmit(e as unknown as React.FormEvent);
                    }
                  }
                }}
                placeholder={t('create.complaint_placeholder')}
                rows={1}
                required
                disabled={isLoading}
              />
              <div className="chat-input-footer">
                {lastResult && (
                  <button type="button" className="btn btn-ghost" onClick={handleNewCase} style={{ marginRight: 'auto' }}>
                    {t('create.result_new')}
                  </button>
                )}
                <button className="btn btn-primary chat-submit" type="submit" disabled={isLoading || !draft.trim()}>
                  {isLoading ? t('create.submitting') : t('create.submit')}
                </button>
              </div>
            </form>
          )}
        </div>

        {/* Quick scenario chips */}
        {messages.length === 0 && !isViewingHistory && (
          <div className="chip-bar">
            {(['chip_train', 'chip_bill', 'chip_provider', 'chip_conflict'] as const).map(chip => (
              <button
                key={chip}
                className="scenario-chip"
                onClick={() => setDraft(CHIP_COMPLAINTS[chip])}
              >
                {t(`create.${chip}`)}
              </button>
            ))}
          </div>
        )}

        {/* Advanced fields — collapsible */}
        {!lastResult && !isViewingHistory && (
          <details className="advanced-section" open={showAdvanced} onToggle={e => setShowAdvanced((e.target as HTMLDetailsElement).open)}>
            <summary className="advanced-toggle">{t('create.advanced_title')}</summary>
            <div className="advanced-fields">
              <div className="advanced-row">
                <div className="form-group">
                  <label className="form-label">{t('create.user_id_label')}</label>
                  <input className="form-input" value={userId} onChange={e => setUserId(e.target.value)} placeholder={t('create.user_id_placeholder')} disabled={isLoading} />
                </div>
                <div className="form-group">
                  <label className="form-label">{t('create.txn_id_label')}</label>
                  <input className="form-input" value={txnId} onChange={e => setTxnId(e.target.value)} placeholder={t('create.txn_id_placeholder')} disabled={isLoading} />
                </div>
                <div className="form-group">
                  <label className="form-label">{t('create.service_label')}</label>
                  <select className="form-select" value={serviceType} onChange={e => setServiceType(e.target.value)} disabled={isLoading}>
                    <option value="">{t('create.service_auto')}</option>
                    <option value="train_ticket">{t('service.train_ticket')}</option>
                    <option value="electric_bill">{t('service.electric_bill')}</option>
                    <option value="water_bill">{t('service.water_bill')}</option>
                  </select>
                </div>
              </div>
            </div>
          </details>
        )}
      </div>

      {/* ════════════ RIGHT COLUMN: Agent Understanding Panel ════════════ */}
      <div className="intake-right">
        {/* Extraction Preview */}
        <div className="panel-card">
          <h4 className="panel-title">🔍 {t('create.panel_extract')}</h4>
          {!lastResult ? (
            <div className="panel-empty">
              <div className="panel-empty-icon">📋</div>
              <p>{t('create.panel_extract_empty')}</p>
            </div>
          ) : lastResult.extracted_info ? (
            <div className="panel-list">
              <div className="panel-row"><span>Transaction ID</span><span className="mono">{lastResult.extracted_info.transaction_id || '—'}</span></div>
              <div className="panel-row"><span>User ID</span><span className="mono">{lastResult.extracted_info.user_id || '—'}</span></div>
              <div className="panel-row"><span>Service</span><span>{t(`service.${lastResult.extracted_info.service_type}`)}</span></div>
              <div className="panel-row"><span>Issue</span><span>{t(`issue.${lastResult.extracted_info.issue_type}`)}</span></div>
              <div className="panel-row"><span>Amount</span><span>{lastResult.extracted_info.amount_claimed != null ? formatCurrency(lastResult.extracted_info.amount_claimed) : '—'}</span></div>
              {lastResult.extracted_info.bill_code && <div className="panel-row"><span>Bill Code</span><span className="mono">{lastResult.extracted_info.bill_code}</span></div>}
              {lastResult.extracted_info.order_id && <div className="panel-row"><span>Order ID</span><span className="mono">{lastResult.extracted_info.order_id}</span></div>}
            </div>
          ) : null}
        </div>

        {/* Workflow Routing */}
        <div className="panel-card">
          <h4 className="panel-title">⚙️ {t('create.panel_workflow')}</h4>
          <div className="panel-list">
            {(['train_ticket', 'utility_bill', 'train_ticket_reconciliation', 'utility_bill_reconciliation'] as const).map(wf => (
              <div key={wf} className={`workflow-item ${lastResult?.selected_workflow === wf ? 'active' : ''}`}>
                <span className="workflow-dot" />
                {t(`workflow.${wf}`)}
                {lastResult?.selected_workflow === wf && <span className="badge badge-blue" style={{ marginLeft: 'auto', fontSize: '0.65rem' }}>✓</span>}
              </div>
            ))}
            <div className={`workflow-item ${lastResult?.recommended_action === 'manual_review' ? 'active danger' : ''}`}>
              <span className="workflow-dot danger" />
              Manual Review
              {lastResult?.recommended_action === 'manual_review' && <span className="badge badge-red" style={{ marginLeft: 'auto', fontSize: '0.65rem' }}>✓</span>}
            </div>
          </div>
        </div>

        {/* Safety Boundaries */}
        <div className="panel-card">
          <h4 className="panel-title">🛡️ {t('create.panel_safety')}</h4>
          <div className="panel-list">
            {[1, 2, 3, 4, 5].map(i => (
              <div key={i} className="safety-row">
                <span className="safety-check">✓</span>
                <span>{t(`create.safety_${i}`)}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Source of Truth */}
        <div className="panel-card">
          <h4 className="panel-title">📊 {t('create.panel_source')}</h4>
          <div className="panel-list">
            <div className="source-row"><span className="source-icon">💰</span><span>{t('create.source_wallet')}</span></div>
            <div className="source-row"><span className="source-icon">🏢</span><span>{t('create.source_provider')}</span></div>
            <div className="source-row"><span className="source-icon">🔄</span><span>{t('create.source_refund')}</span></div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Agent Result Card — extracted as a function component for clarity
   ═══════════════════════════════════════════════════════════════ */

function ResultCard({ result, t, navigate, onNewCase }: {
  result: CaseResponse;
  t: (key: string, params?: Record<string, string | number>) => string;
  navigate: (path: string) => void;
  onNewCase: () => void;
}) {
  const ei = result.extracted_info;
  const isRefund = result.recommended_action === 'create_refund_request_draft';
  const errors = result.errors || [];

  /* ── Classify errors for friendly alerts ── */
  const hasDataNotFound = errors.some(e => e.includes('Data not found'));
  const hasMaxRetries = errors.some(e => e.includes('max retries exceeded'));
  const hasCriticalMissing = errors.some(e => e.includes('critical: transaction_id missing'));
  const hasNoExtraction = !ei?.transaction_id && !ei?.user_id && !ei?.service_type;
  const hasErrors = errors.length > 0;

  /* Choose result avatar style based on outcome */
  const avatarClass = hasErrors && !result.recommended_action
    ? 'bubble-avatar notfound-avatar'
    : 'bubble-avatar result-avatar';
  const avatarIcon = hasErrors && !result.recommended_action ? '!' : '✓';

  return (
    <div className={`agent-bubble result-bubble ${hasErrors && !result.recommended_action ? 'result-bubble-warn' : ''}`}>
      <div className={avatarClass}>{avatarIcon}</div>
      <div className="bubble-content">
        <div className="result-header">{t('create.result_title')}</div>

        {/* ── Friendly error alerts ── */}
        {hasCriticalMissing && (
          <div className="alert alert-warning" style={{ margin: '10px 0', fontSize: '0.8rem' }}>
            <span className="alert-icon">🔍</span>{t('create.alert_no_txn')}
          </div>
        )}
        {hasNoExtraction && !hasCriticalMissing && (
          <div className="alert alert-warning" style={{ margin: '10px 0', fontSize: '0.8rem' }}>
            <span className="alert-icon">📝</span>{t('create.alert_no_extract')}
          </div>
        )}
        {hasDataNotFound && (
          <div className="alert alert-danger" style={{ margin: '10px 0', fontSize: '0.8rem' }}>
            <span className="alert-icon">❌</span>{t('create.alert_not_found')}
          </div>
        )}
        {hasMaxRetries && (
          <div className="alert alert-warning" style={{ margin: '10px 0', fontSize: '0.8rem' }}>
            <span className="alert-icon">🔄</span>{t('create.alert_evidence_fail')}
          </div>
        )}

        {/* ── Standard context alerts ── */}
        {result.has_conflict && (
          <div className="alert alert-danger" style={{ margin: '10px 0', fontSize: '0.8rem' }}>
            <span className="alert-icon">🚨</span>{t('create.alert_conflict')}
          </div>
        )}
        {isRefund && (
          <div className="alert alert-warning" style={{ margin: '10px 0', fontSize: '0.8rem' }}>
            <span className="alert-icon">💰</span>{t('create.alert_refund')}
          </div>
        )}
        {ei && ei.missing_fields.length > 0 && !hasCriticalMissing && (
          <div className="alert alert-info" style={{ margin: '10px 0', fontSize: '0.8rem' }}>
            <span className="alert-icon">ℹ️</span>{t('create.alert_missing', { fields: ei.missing_fields.join(', ') })}
          </div>
        )}

        {/* Result grid */}
        <div className="result-grid">
          <div className="result-row">
            <span className="result-label">{t('create.result_workflow')}</span>
            <span className="result-value">{result.selected_workflow ? t(`workflow.${result.selected_workflow}`) : '—'}</span>
          </div>
          <div className="result-row">
            <span className="result-label">{t('create.result_action')}</span>
            <span className="result-value">{result.recommended_action ? <ActionBadge action={result.recommended_action} /> : '—'}</span>
          </div>
          <div className="result-row">
            <span className="result-label">{t('create.result_risk')}</span>
            <span className="result-value">{result.risk_level ? <RiskBadge level={result.risk_level} /> : '—'}</span>
          </div>
          <div className="result-row">
            <span className="result-label">{t('create.result_approval')}</span>
            <span className="result-value">{result.approval_required ? t('common.yes') : t('common.no')}</span>
          </div>
          <div className="result-row">
            <span className="result-label">{t('create.result_conflict')}</span>
            <span className="result-value">{result.has_conflict ? <ConflictBadge hasConflict /> : '—'}</span>
          </div>
          <div className="result-row">
            <span className="result-label">{t('dashboard.col_status')}</span>
            <span className="result-value"><StatusBadge status={result.status} /></span>
          </div>
        </div>

        {/* Extracted info */}
        {ei && (
          <div className="result-extracted">
            <div className="result-row"><span className="result-label">User</span><span className="result-value mono">{ei.user_id || '—'}</span></div>
            <div className="result-row"><span className="result-label">Transaction</span><span className="result-value mono">{ei.transaction_id || '—'}</span></div>
            <div className="result-row"><span className="result-label">Service</span><span className="result-value">{ei.service_type ? t(`service.${ei.service_type}`) : '—'}</span></div>
            <div className="result-row"><span className="result-label">Issue</span><span className="result-value">{ei.issue_type ? t(`issue.${ei.issue_type}`) : '—'}</span></div>
            {ei.amount_claimed != null && (
              <div className="result-row"><span className="result-label">Amount</span><span className="result-value" style={{ color: 'var(--amber)' }}>{formatCurrency(ei.amount_claimed)}</span></div>
            )}
          </div>
        )}

        {/* Collapsible raw errors (for power users) */}
        {hasErrors && (
          <details className="error-details">
            <summary className="error-details-toggle">{t('create.alert_errors_title')} ({errors.length})</summary>
            <ul className="error-details-list">
              {errors.map((err, i) => <li key={i}>{err}</li>)}
            </ul>
          </details>
        )}

        {/* Action buttons */}
        <div className="result-actions">
          <button className="btn btn-primary" onClick={() => navigate(`/cases/${result.case_id}`)}>
            {t('create.result_view_detail')}
          </button>
          <button className="btn btn-ghost" onClick={onNewCase}>
            {t('create.result_new')}
          </button>
        </div>
      </div>
    </div>
  );
}
