/* ─── Back-office Customer-Chat Handoff Tickets API ─── */

import { api } from './client';

export interface ChatTicketRow {
  ticket_id: string;
  source: string;
  subject_type: string;
  complainant_display_name: string;
  complainant_phone: string;
  complainant_email: string;
  complainant_user_id: string;
  complainant_merchant_id: string;
  selected_workflow: string;
  issue_type: string;
  risk_level: string;
  approval_required: boolean;
  recommended_action: string;
  backoffice_ticket_status: string;
  created_at: string;
  updated_at: string;
}

export interface ChatTicketListResponse {
  tickets: ChatTicketRow[];
  total: number;
}

export interface ChatMessagePublic {
  role: string;
  text: string;
  timestamp: string;
}

export interface ComplainantPublic {
  subject_type: string;
  display_name: string;
  phone: string;
  email: string;
  user_id: string;
  wallet_id: string;
  account_status: string;
  wallet_status: string;
  merchant_name: string;
  merchant_id: string;
  tax_code: string;
  bank_account_status: string;
  settlement_cycle: string;
}

/* ─── New structured models for action-oriented staff UX ─── */

export interface AgentDiagnosisPublic {
  what_was_checked: string[];
  confirmed_facts: string[];
  money_or_issue_location: string;
  likely_bottleneck: string;
  confidence: string;
  why_staff_action_needed: string;
  missing_evidence: string[];
}

export interface ResolvedEntity {
  type?: string;
  id?: string;
}

export interface EvidenceCheckItem {
  label: string;
  status: 'checked' | 'missing' | 'needs_review';
  detail: string;
}

export interface StaffAction {
  // Core contract
  action_title: string;
  action_type: string;
  approval_required: boolean;
  next_owner_team: string;
  why_recommended: string;
  approve_button_label: string;
  // Effects / safety
  approve_effect: string[];
  approve_does_not_do: string[];
  preconditions_checked: string[];
  missing_preconditions: string[];
  safety_warnings: string[];
  next_status_after_approve: string;
  audit_event_type: string;
  // Legacy compat
  recommended_action_label: string;
  recommended_action_type: string;
  required_preconditions: string[];
}

export interface TicketHeaderPublic {
  ticket_id: string;
  source: string;
  selected_workflow: string;
  backoffice_ticket_status: string;
  risk_level: string;
  approval_required: boolean;
  created_at: string;
  updated_at: string;
  summary: string;
}

export interface CustomerProblemPublic {
  original_complaint: string;
  latest_customer_message: string;
  customer_emotion: string;
  extracted_amount: string;
  extracted_time: string;
  extracted_bank_provider: string;
  extracted_transaction_id: string;
  extracted_order_id: string;
  extracted_bill_code: string;
  extracted_payout_id: string;
}

export interface AuditLogEntry {
  actor: string;
  action: string;
  timestamp: string;
  comment: string;
}

/* ─── Main ticket detail (backward-compatible + new structured fields) ─── */

export interface ChatTicketDetail {
  ticket_id: string;
  source: string;
  customer_chat_case_id: string;
  public_case_ref: string;
  subject_type: string;
  complainant: ComplainantPublic;
  conversation_summary: string;
  customer_problem: string;
  customer_emotion: string;
  key_customer_claims: string[];
  customer_provided_info: string[];
  latest_customer_message: string;
  timeline: ChatMessagePublic[];
  selected_workflow: string;
  issue_type: string;
  public_safe_diagnosis: Record<string, unknown>;
  diagnosis_confidence: string;
  internal_staff_evidence_summary: Record<string, unknown>;
  recommended_action: string;
  approval_required: boolean;
  risk_level: string;
  linked_action_draft_id: string;
  backoffice_ticket_status: string;
  handoff_reason: string;
  created_at: string;
  updated_at: string;
  /* Actual extracted values (amount/time/bank/ids) — never placeholder labels */
  extracted_info?: Record<string, unknown>;
  /* Investigation result (explicit, staff-facing) */
  resolved_entity?: ResolvedEntity;
  money_or_issue_location?: string;
  missing_evidence?: string[];
  /* NEW structured fields */
  ticket_header?: TicketHeaderPublic | null;
  customer_problem_structured?: CustomerProblemPublic | null;
  agent_diagnosis?: AgentDiagnosisPublic | null;
  evidence_checklist?: EvidenceCheckItem[];
  staff_action?: StaffAction | null;
  conversation_timeline?: ChatMessagePublic[];
  audit_entries?: AuditLogEntry[];
}

export interface ChatTicketFilters {
  source?: string;
  workflow?: string;
  status?: string;
  risk_level?: string;
  approval_required?: boolean;
  subject_type?: string;
  created_from?: string;
  created_to?: string;
  assigned_team?: string;
  q?: string;
}

function buildQuery(filters: ChatTicketFilters): string {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') {
      params.append(k, String(v));
    }
  });
  const s = params.toString();
  return s ? `?${s}` : '';
}

export const chatTicketsApi = {
  list: (filters: ChatTicketFilters = {}) =>
    api.get<ChatTicketListResponse>(
      `/api/backoffice/chat-tickets${buildQuery(filters)}`,
    ),

  get: (ticketId: string) =>
    api.get<ChatTicketDetail>(`/api/backoffice/chat-tickets/${ticketId}`),

  approve: (ticketId: string, actor: string, comment?: string) =>
    api.post<ChatTicketDetail>(
      `/api/backoffice/chat-tickets/${ticketId}/approve`, { actor, comment },
    ),

  reject: (ticketId: string, actor: string, comment?: string) =>
    api.post<ChatTicketDetail>(
      `/api/backoffice/chat-tickets/${ticketId}/reject`, { actor, comment },
    ),

  requestInfo: (ticketId: string, actor: string, comment?: string) =>
    api.post<ChatTicketDetail>(
      `/api/backoffice/chat-tickets/${ticketId}/request-info`, { actor, comment },
    ),
};

