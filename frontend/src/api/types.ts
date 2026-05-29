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
