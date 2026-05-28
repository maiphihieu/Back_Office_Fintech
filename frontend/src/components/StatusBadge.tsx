/* ─── Status / Risk / Conflict Badges (i18n) ─── */

import { useI18n } from '../lib/i18n';

export function StatusBadge({ status }: { status: string | null }) {
  const { t } = useI18n();
  if (!status) return null;
  const map: Record<string, string> = {
    new: 'badge-blue',
    extracting: 'badge-blue',
    fetching_evidence: 'badge-blue',
    waiting_approval: 'badge-amber',
    closed: 'badge-green',
    manual_review: 'badge-red',
    dead_letter: 'badge-neutral',
    missing_info: 'badge-amber',
    conflict_detected: 'badge-red',
  };
  const cls = map[status] || 'badge-neutral';
  const label = t(`status.${status}`);
  return <span className={`badge ${cls}`}>{label}</span>;
}

export function RiskBadge({ level }: { level: string | null }) {
  const { t } = useI18n();
  if (!level) return null;
  const map: Record<string, string> = {
    low: 'badge-green',
    medium: 'badge-amber',
    high: 'badge-red',
    critical: 'badge-red',
  };
  const cls = map[level] || 'badge-neutral';
  const label = t(`risk.${level}`);
  return <span className={`badge ${cls}`}>⚡ {label}</span>;
}

export function ConflictBadge({ hasConflict }: { hasConflict: boolean }) {
  const { t } = useI18n();
  if (!hasConflict) return null;
  return <span className="badge badge-red">{t('badge.conflict')}</span>;
}

export function ApprovalBadge({ required }: { required: boolean }) {
  const { t } = useI18n();
  if (!required) return null;
  return <span className="badge badge-amber">{t('badge.approval_required')}</span>;
}

export function ActionBadge({ action }: { action: string | null }) {
  const { t } = useI18n();
  if (!action) return null;
  const map: Record<string, string> = {
    create_refund_request_draft: 'badge-amber',
    draft_customer_response: 'badge-green',
    create_reconciliation_ticket_draft: 'badge-blue',
    manual_review: 'badge-red',
    no_action: 'badge-neutral',
    wait_sla: 'badge-cyan',
  };
  const cls = map[action] || 'badge-neutral';
  const label = t(`action.${action}`);
  return <span className={`badge ${cls}`}>{label}</span>;
}
