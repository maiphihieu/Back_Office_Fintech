/* ─── Customer Chat API — sanitized endpoint only ─── */

import { api } from './client';

export interface CustomerChatRequest {
  message: string;
  session_id?: string | null;
}

export interface CustomerChatResponse {
  public_case_id: string;
  status: 'received' | 'need_more_info' | 'processing' | 'need_login';
  public_response: string;
  missing_info_questions: string[];
}

export const customerChatApi = {
  /**
   * Submit a customer complaint.
   *
   * Returns ONLY sanitized public data:
   * - public_case_id
   * - status (received | need_more_info | processing | need_login)
   * - public_response (safe customer-facing text)
   * - missing_info_questions (safe follow-up questions)
   *
   * Does NOT return: evidence, rule_decision, approval_packet,
   * action_draft, audit logs, risk_score, MCP results, or any
   * internal back-office data.
   */
  submit: (data: CustomerChatRequest) =>
    api.post<CustomerChatResponse>('/api/customer-chat', data),

  /**
   * Finalize the chat into ONE back-office ticket (deduped server-side).
   * Call on explicit chat end ("ended") or session TTL expiry ("expired").
   * Do NOT call on a quick popup close+reopen.
   */
  handoff: (sessionId: string, reason: 'ended' | 'expired' | 'staff_request') =>
    api.post<{ handed_off: boolean; public_case_ref: string; message: string }>(
      '/api/customer-chat/handoff',
      { session_id: sessionId, reason },
    ),
};
