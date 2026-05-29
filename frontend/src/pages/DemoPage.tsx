/* ─── Demo Scenarios Page (i18n) ─── */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { casesApi } from '../api/cases';
import type { CaseResponse } from '../api/types';
import { ActionBadge, RiskBadge, StatusBadge } from '../components/StatusBadge';
import { DEMO_SCENARIOS, type DemoScenario } from '../lib/constants';
import { useI18n } from '../lib/i18n';

interface DemoResult {
  scenarioId: string;
  caseResponse: CaseResponse | null;
  error: string | null;
  passed: boolean | null;
}

export default function DemoPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [results, setResults] = useState<Record<string, DemoResult>>({});
  const [runningId, setRunningId] = useState<string | null>(null);
  const [runningAll, setRunningAll] = useState(false);

  const runScenario = async (scenario: DemoScenario) => {
    setRunningId(scenario.id);
    try {
      const res = await casesApi.create({
        raw_complaint: scenario.complaint,
        user_id: scenario.user_id,
        transaction_id: 'transaction_id' in scenario ? (scenario as { transaction_id: string }).transaction_id : undefined,
        service_type: scenario.service_type,
      });

      const passed = res.recommended_action === scenario.expected_action &&
                     res.approval_required === scenario.expected_approval;

      setResults(prev => ({
        ...prev,
        [scenario.id]: { scenarioId: scenario.id, caseResponse: res, error: null, passed },
      }));
    } catch (e: unknown) {
      setResults(prev => ({
        ...prev,
        [scenario.id]: {
          scenarioId: scenario.id,
          caseResponse: null,
          error: e instanceof Error ? e.message : t('demo.scenario_error'),
          passed: false,
        },
      }));
    } finally {
      setRunningId(null);
    }
  };

  const runAll = async () => {
    setRunningAll(true);
    for (const scenario of DEMO_SCENARIOS) {
      await runScenario(scenario);
    }
    setRunningAll(false);
  };

  const allRun = DEMO_SCENARIOS.every(s => s.id in results);
  const allPassed = allRun && DEMO_SCENARIOS.every(s => results[s.id]?.passed);

  return (
    <div>
      <div className="page-header">
        <h1>{t('demo.title')}</h1>
        <div className="page-header-actions">
          <button className="btn btn-primary" onClick={runAll} disabled={runningAll || runningId !== null}>
            {runningAll ? t('demo.running_all') : t('demo.run_all')}
          </button>
        </div>
      </div>

      {allRun && (
        <div className={`alert ${allPassed ? 'alert-success' : 'alert-danger'} mb-3`}>
          <span className="alert-icon">{allPassed ? '🎉' : '⚠'}</span>
          <span>{allPassed ? t('demo.all_passed') : t('demo.some_failed')}</span>
        </div>
      )}

      <div className="demo-grid">
        {DEMO_SCENARIOS.map((scenario) => {
          const result = results[scenario.id];
          const isRunning = runningId === scenario.id;

          return (
            <div key={scenario.id} className="demo-card">
              <div className="flex justify-between items-center mb-2">
                <h3>{scenario.id}</h3>
                {result?.passed === true && <span className="badge badge-green">{t('demo.pass')}</span>}
                {result?.passed === false && <span className="badge badge-red">{t('demo.fail')}</span>}
              </div>
              <h4 className="text-sm mb-2" style={{ color: 'var(--text-primary)' }}>{t(`demo.${scenario.id}_title`)}</h4>
              <p>{t(`demo.${scenario.id}_desc`)}</p>

              <div className="detail-row"><span className="label">{t('demo.expected_action')}</span><span className="value text-xs">{t(`action.${scenario.expected_action}`)}</span></div>
              <div className="detail-row"><span className="label">{t('demo.expected_approval')}</span><span className="value">{scenario.expected_approval ? t('common.yes') : t('common.no')}</span></div>
              <div className="detail-row"><span className="label">{t('demo.expected_risk')}</span><span className="value">{t(`risk.${scenario.expected_risk}`)}</span></div>

              <div className="mt-3">
                <button
                  className="btn btn-ghost"
                  onClick={() => runScenario(scenario)}
                  disabled={isRunning || runningAll}
                  style={{ width: '100%', justifyContent: 'center' }}
                >
                  {isRunning ? t('demo.running') : t('demo.run_scenario')}
                </button>
              </div>

              {result?.caseResponse && (
                <div className="demo-result">
                  <div className="flex gap-2 mb-2" style={{ flexWrap: 'wrap' }}>
                    <StatusBadge status={result.caseResponse.status} />
                    <ActionBadge action={result.caseResponse.recommended_action} />
                    <RiskBadge level={result.caseResponse.risk_level} />
                  </div>
                  <div className="detail-row"><span className="label">{t('demo.workflow')}</span><span className="value">{t(`workflow.${result.caseResponse.selected_workflow}`)}</span></div>
                  <div className="detail-row"><span className="label">{t('demo.approval')}</span><span className="value">{result.caseResponse.approval_required ? t('common.yes') : t('common.no')}</span></div>
                  <button
                    className="btn btn-ghost mt-2"
                    style={{ width: '100%', justifyContent: 'center', fontSize: '0.78rem' }}
                    onClick={() => navigate(`/cases/${result.caseResponse!.case_id}`)}
                  >
                    {t('demo.view_detail')}
                  </button>
                </div>
              )}

              {result?.error && (
                <div className="alert alert-danger mt-2" style={{ fontSize: '0.8rem' }}>
                  <span className="alert-icon">⚠</span>{result.error}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
