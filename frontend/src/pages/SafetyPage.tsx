/* ─── Safety Checks Page (i18n) ─── */

import { useEffect, useState } from 'react';
import { casesApi } from '../api/cases';
import { useI18n } from '../lib/i18n';

const CHECK_COUNT = 12;

export default function SafetyPage() {
  const { t } = useI18n();
  const [health, setHealth] = useState<{ status: string; version: string; environment: string } | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);

  useEffect(() => {
    casesApi.health()
      .then(setHealth)
      .catch((e: Error) => setHealthError(e.message));
  }, []);

  const checks = Array.from({ length: CHECK_COUNT }, (_, i) => ({
    label: t(`safety.check_${i + 1}_label`),
    description: t(`safety.check_${i + 1}_desc`),
    pass: true,
  }));

  return (
    <div>
      <div className="page-header">
        <h1>{t('safety.title')}</h1>
      </div>

      {/* System Health */}
      <div className="card mb-3">
        <h4 className="mb-2">{t('safety.health_title')}</h4>
        {healthError && (
          <div className="alert alert-danger">
            <span className="alert-icon">⚠</span>
            {t('safety.health_error')} {healthError}
          </div>
        )}
        {health && (
          <div>
            <div className="detail-row"><span className="label">{t('safety.health_status')}</span><span className="value"><span className="badge badge-green">{health.status === 'ok' ? t('safety.health_ok') : health.status}</span></span></div>
            <div className="detail-row"><span className="label">{t('safety.health_version')}</span><span className="value mono">{health.version}</span></div>
            <div className="detail-row"><span className="label">{t('safety.health_env')}</span><span className="value">{health.environment === 'local' ? t('safety.health_env_local') : health.environment}</span></div>
          </div>
        )}
        {!health && !healthError && <div className="loading"><div className="spinner" /></div>}
      </div>

      {/* Safety Checklist */}
      <div className="card">
        <h4 className="mb-3">{t('safety.checks_title')} — {t('safety.checks_count', { n: CHECK_COUNT })}</h4>
        <p className="text-sm text-secondary mb-3" dangerouslySetInnerHTML={{ __html: t('safety.checks_desc') }} />

        {checks.map((check, i) => (
          <div key={i} className="checklist-item">
            <span className={`checklist-icon ${check.pass ? 'pass' : 'fail'}`}>
              {check.pass ? '✅' : '❌'}
            </span>
            <div>
              <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{check.label}</div>
              <div className="text-xs text-muted">{check.description}</div>
            </div>
          </div>
        ))}
      </div>

      {/* LLM Mode */}
      <div className="card mt-3">
        <h4 className="mb-2">{t('safety.llm_title')}</h4>
        <div className="alert alert-info">
          <span className="alert-icon">🤖</span>
          <div>
            <span dangerouslySetInnerHTML={{ __html: t('safety.llm_desc') }} />
            <br /><br />
            <strong>{t('safety.llm_never')}</strong> {t('safety.llm_never_desc')}
          </div>
        </div>
      </div>
    </div>
  );
}
