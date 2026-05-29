/* ─── History Store — abstraction over backend GET /cases ─── */
/* Maps CaseResponse[] to lightweight CaseHistoryItem[] for the sidebar.
   When backend moves to persistent storage, this layer stays unchanged. */

import type { CaseResponse } from '../api/types';

export type CaseHistoryItem = {
  id: string;
  caseId: string;
  title: string;
  rawComplaint: string;
  transactionId: string | null;
  serviceType: string | null;
  selectedWorkflow: string | null;
  recommendedAction: string | null;
  riskLevel: string | null;
  status: string;
  hasConflict: boolean;
  approvalRequired: boolean;
  errorCount: number;
  createdAt: string; // ISO string or relative
};

/* ─── Helpers ─── */

/** Generate a short title from the complaint text (max 30 chars). */
export function generateTitle(complaint: string | null): string {
  if (!complaint) return '—';
  const cleaned = complaint.replace(/\s+/g, ' ').trim();
  if (cleaned.length <= 30) return cleaned;
  return cleaned.slice(0, 28).trimEnd() + '…';
}

/** Get emoji icon for a service type. */
export function getServiceIcon(serviceType?: string | null, workflow?: string | null): string {
  const key = serviceType || workflow || '';
  if (key.includes('train')) return '🚆';
  if (key.includes('electricity') || key.includes('electric') || key.includes('điện')) return '💡';
  if (key.includes('water') || key.includes('nước')) return '💧';
  if (key.includes('utility') || key.includes('bill')) return '📄';
  if (key.includes('wallet') || key.includes('topup')) return '💳';
  if (key.includes('reconciliation')) return '🔄';
  return '📋';
}

/** Convert CaseResponse array to CaseHistoryItem array (newest first). */
export function casesToHistoryItems(cases: CaseResponse[]): CaseHistoryItem[] {
  return cases
    .map((c): CaseHistoryItem => ({
      id: c.case_id,
      caseId: c.case_id,
      title: generateTitle(c.raw_complaint),
      rawComplaint: c.raw_complaint || '',
      transactionId: c.extracted_info?.transaction_id ?? null,
      serviceType: c.extracted_info?.service_type ?? null,
      selectedWorkflow: c.selected_workflow,
      recommendedAction: c.recommended_action,
      riskLevel: c.risk_level,
      status: c.status,
      hasConflict: c.has_conflict,
      approvalRequired: c.approval_required,
      errorCount: c.errors?.length ?? 0,
      createdAt: new Date().toISOString(), // MVP: backend doesn't track timestamps
    }))
    .reverse(); // newest first (backend stores in insertion order)
}
