/* ─── API Types — mirrors backend Pydantic models ─── */

export interface ExtractedInfo {
  user_id: string | null;
  transaction_id: string | null;
  service_type: string | null;
  issue_type: string | null;
  order_id: string | null;
  bill_code: string | null;
  customer_code: string | null;
  amount_claimed: number | null;
  language: string | null;
  confidence: number | null;
  extraction_method: string | null;
  missing_fields: string[];
  phone?: string | null;
  email?: string | null;
  wallet_id?: string | null;
}

export interface EvidenceBundle {
  transaction: Record<string, unknown> | null;
  wallet_ledger: Record<string, unknown> | null;
  provider_status: Record<string, unknown> | null;
  refund_status: Record<string, unknown> | null;
  reconciliation_status: Record<string, unknown> | null;
  account_status?: {
    user_id: string;
    wallet_id?: string;
    account_status?: string;
    withdrawal_enabled?: boolean;
    lock_reason?: string;
    current_balance?: number;
    locked_at?: string;
  } | null;
  fraud_case?: {
    fraud_case_id: string;
    user_id: string;
    risk_score?: number;
    risk_level?: string;
    fraud_status?: string;
    trigger_reason?: string;
    signals?: Record<string, unknown>;
    recent_transactions?: Array<Record<string, unknown>>;
    device_events?: Array<Record<string, unknown>>;
    recommended_decision?: string;
  } | null;
}

export interface Conflict {
  conflict_type: string;
  description: string;
  source_a: string | null;
  source_b: string | null;
}

export interface ResponseDebug {
  generation_mode: 'llm' | 'fallback';
  fallback_reason: string | null;
  llm_error: string | null;
  model_used: string | null;
}

export interface GeneratedResponse {
  case_summary: string;
  problem_location: string;
  problem_explanation: string;
  evidence_checked: string[];
  evidence_supporting_problem_location: string[];
  problem_location_confidence: string;
  internal_summary: string;
  recommended_next_step: string;
  customer_reply_draft: string;
  safety_notes: string[];
  debug?: ResponseDebug | null;
}

export interface TicketAction {
  action_id: string;
  action_name: string;
  action_type: string;
  description: string;
  mcp_tool: string | null;
  mcp_input: Record<string, unknown> | null;
  preconditions: string[];
  evidence_dependencies: string[];
  requires_approval: boolean;
  approval_status: string;
  execution_mode: string;
  risk_level: string;
  reason: string;
  status: string;
  expected_result: string;
  safety_notes: string[];
  staff_instruction: string;
}

export interface ResolutionTicket {
  ticket_id: string;
  ticket_type: string;
  issue_summary: string;
  problem_location: string;
  problem_explanation: string;
  evidence_checked: string[];
  missing_evidence: string[];
  resolution_status: string;
  recommended_actions: TicketAction[];
  staff_instruction: string;
  customer_reply_draft: string;
  safety_notes: string[];
  amount_verification?: AmountVerification | null;
  claim_verification?: ClaimVerificationSummary | null;
}

export interface AmountVerification {
  customer_claimed_amount: number | null;
  trusted_amount: number | null;
  trusted_amount_source: string | null;
  action_amount: number | null;
  action_amount_source: string | null;
  has_amount_mismatch: boolean;
  mismatch_description: string;
}

export interface ClaimVerification {
  claim_id: string;
  claim_type: string;
  raw_text: string;
  customer_claimed_value: string | number | null;
  normalized_value: string | number | null;
  unit: string | null;
  confidence: number;
  verification_status: 'matched' | 'mismatched' | 'not_verifiable' | 'not_found' | 'system_only';
  trusted_system_value: string | number | null;
  trusted_source: string | null;
  explanation: string;
}

export interface ClaimVerificationSummary {
  summary: string;
  claims: ClaimVerification[];
  matched_claims: string[];
  mismatched_claims: string[];
  not_verifiable_claims: string[];
  not_found_claims: string[];
  has_customer_detail_mismatch: boolean;
  has_system_evidence_conflict: boolean;
  staff_explanation: string;
  trusted_data_used_for_action: Record<string, string | number | null>;
}

export interface CaseResponse {
  case_id: string;
  status: string;
  user_id: string | null;
  selected_workflow: string | null;
  recommended_action: string | null;
  diagnosis: string | null;
  diagnosis_message: string | null;
  risk_level: string | null;
  approval_required: boolean;
  approval_status: string | null;
  has_conflict: boolean;
  conflicts: Conflict[];
  extracted_info: ExtractedInfo | null;
  evidence: EvidenceBundle | null;
  draft_output: Record<string, unknown> | null;
  generated_response?: GeneratedResponse | null;
  resolution_ticket?: ResolutionTicket | null;
  errors: string[];
  next_step: string;
  raw_complaint: string | null;
  audit_event_count: number;
}

export interface CaseListResponse {
  total: number;
  cases: CaseResponse[];
}

export interface AuditEvent {
  event_id: string;
  event_type: string;
  actor: string;
  timestamp: string;
  previous_status: string | null;
  new_status: string | null;
  details: Record<string, unknown>;
}

export interface AuditTrailResponse {
  case_id: string;
  event_count: number;
  events: AuditEvent[];
}

export interface CreateCaseRequest {
  raw_complaint: string;
  user_id?: string;
  transaction_id?: string;
  service_type?: string;
}

export interface ApproveRequest {
  approver: string;
  comment?: string;
}

export interface RejectRequest {
  approver: string;
  reason: string;
}

export interface HealthResponse {
  status: string;
  version: string;
  environment: string;
}
