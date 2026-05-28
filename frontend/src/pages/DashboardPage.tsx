/* ─── Dashboard / Case List Page (i18n) ─── */

import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { casesApi } from '../api/cases';
import type { CaseResponse } from '../api/types';
import { ActionBadge, ApprovalBadge, ConflictBadge, RiskBadge, StatusBadge } from '../components/StatusBadge';
import { useI18n } from '../lib/i18n';

export default function DashboardPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [cases, setCases] = useState<CaseResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /* Filters */
  const [filterStatus, setFilterStatus] = useState('');
  const [filterService, setFilterService] = useState('');
  const [filterRisk, setFilterRisk] = useState('');
  const [filterApproval, setFilterApproval] = useState('');
  const [filterConflict, setFilterConflict] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await casesApi.list();
      setCases(res.cases);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('dashboard.load_error'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- data fetch on mount
  useEffect(() => { void load(); }, [load]);

  const filtered = cases.filter((c) => {
    if (filterStatus && c.status !== filterStatus) return false;
    if (filterService && c.selected_workflow !== filterService) return false;
    if (filterRisk && c.risk_level !== filterRisk) return false;
    if (filterApproval === 'yes' && !c.approval_required) return false;
    if (filterApproval === 'no' && c.approval_required) return false;
    if (filterConflict && !c.has_conflict) return false;
    return true;
  });

  return (
    <div>
      <div className="page-header">
        <h1>{t('dashboard.title')}</h1>
        <div className="page-header-actions">
          <button className="btn btn-ghost" onClick={load}>{t('common.refresh')}</button>
          <button className="btn btn-primary" onClick={() => navigate('/create')}>
            {t('dashboard.create_btn')}
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="filters-bar">
        <select className="form-select" value={filterStatus} onChange={e => setFilterStatus(e.target.value)}>
          <option value="">{t('dashboard.filter_all_status')}</option>
          <option value="new">{t('status.new')}</option>
          <option value="waiting_approval">{t('status.waiting_approval')}</option>
          <option value="closed">{t('status.closed')}</option>
          <option value="manual_review">{t('status.manual_review')}</option>
          <option value="missing_info">{t('status.missing_info')}</option>
        </select>
        <select className="form-select" value={filterService} onChange={e => setFilterService(e.target.value)}>
          <option value="">{t('dashboard.filter_all_workflow')}</option>
          <option value="train_ticket">{t('workflow.train_ticket')}</option>
          <option value="utility_bill">{t('workflow.utility_bill')}</option>
        </select>
        <select className="form-select" value={filterRisk} onChange={e => setFilterRisk(e.target.value)}>
          <option value="">{t('dashboard.filter_all_risk')}</option>
          <option value="low">{t('risk.low')}</option>
          <option value="medium">{t('risk.medium')}</option>
          <option value="high">{t('risk.high')}</option>
        </select>
        <select className="form-select" value={filterApproval} onChange={e => setFilterApproval(e.target.value)}>
          <option value="">{t('dashboard.filter_all_approval')}</option>
          <option value="yes">{t('dashboard.filter_requires_approval')}</option>
          <option value="no">{t('dashboard.filter_no_approval')}</option>
        </select>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.8rem', color: 'var(--text-secondary)', cursor: 'pointer' }}>
          <input type="checkbox" checked={filterConflict} onChange={e => setFilterConflict(e.target.checked)} />
          {t('dashboard.filter_conflict_only')}
        </label>
        <span className="text-xs text-muted" style={{ marginLeft: 'auto' }}>
          {t('dashboard.case_count', { filtered: filtered.length, total: cases.length })}
        </span>
      </div>

      {loading && <div className="loading"><div className="spinner" /></div>}

      {error && <div className="alert alert-danger"><span className="alert-icon">⚠</span>{error}</div>}

      {!loading && !error && filtered.length === 0 && (
        <div className="empty-state">
          <div className="icon">📭</div>
          <p>{t('dashboard.empty')}</p>
        </div>
      )}

      {!loading && filtered.length > 0 && (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('dashboard.col_case_id')}</th>
                  <th>{t('dashboard.col_user')}</th>
                  <th>{t('dashboard.col_workflow')}</th>
                  <th>{t('dashboard.col_action')}</th>
                  <th>{t('dashboard.col_risk')}</th>
                  <th>{t('dashboard.col_approval')}</th>
                  <th>{t('dashboard.col_status')}</th>
                  <th>{t('dashboard.col_flags')}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((c) => (
                  <tr key={c.case_id} onClick={() => navigate(`/cases/${c.case_id}`)}>
                    <td><code className="mono" style={{ color: 'var(--text-accent)' }}>{c.case_id.slice(0, 12)}…</code></td>
                    <td>{c.user_id || '—'}</td>
                    <td>{t(`workflow.${c.selected_workflow}`)}</td>
                    <td><ActionBadge action={c.recommended_action} /></td>
                    <td><RiskBadge level={c.risk_level} /></td>
                    <td>{c.approval_required ? <ApprovalBadge required /> : <span className="text-muted text-xs">—</span>}</td>
                    <td><StatusBadge status={c.status} /></td>
                    <td><ConflictBadge hasConflict={c.has_conflict} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
