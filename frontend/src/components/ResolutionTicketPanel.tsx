/* ─── Resolution Ticket Panel — staff-friendly view for CS/Ops ─── */
/* Actions come from rule engine, not LLM.
   Staff-friendly labels shown by default.
   Technical details (mcp_tool, mcp_input, etc.) hidden under accordion. */

import { useState } from 'react';
import type { GeneratedResponse, ResolutionTicket, TicketAction } from '../api/types';
import { formatCurrency } from '../lib/format';

interface Props {
  ticket: ResolutionTicket | null | undefined;
  aiResponse?: GeneratedResponse | null;
}

/* ═══════════════════════════════════════════════════════════════
   STAFF-FRIENDLY LABEL MAPS
   ═══════════════════════════════════════════════════════════════ */

const RESOLUTION_STATUS: Record<string, { icon: string; label: string; cls: string }> = {
  actionable: { icon: '✅', label: 'Có thể xử lý theo quy trình', cls: 'badge-green' },
  manual_review_required: { icon: '👁', label: 'Cần nhân viên kiểm tra thủ công', cls: 'badge-amber' },
  missing_identity: { icon: '🔍', label: 'Chưa định danh được tài khoản — cần bổ sung thông tin', cls: 'badge-amber' },
  not_supported: { icon: '❌', label: 'Chưa hỗ trợ xử lý tự động', cls: 'badge-red' },
};

const LOCATION_LABELS: Record<string, { icon: string; label: string }> = {
  wallet_system: { icon: '💰', label: 'Hệ thống ví' },
  bank: { icon: '🏦', label: 'Ngân hàng' },
  provider: { icon: '🏢', label: 'Nhà cung cấp dịch vụ' },
  fraud_system: { icon: '🚨', label: 'Hệ thống chống gian lận' },
  identity_lookup: { icon: '🔍', label: 'Tra cứu định danh tài khoản' },
  reconciliation: { icon: '🔄', label: 'Đối soát dữ liệu' },
  customer_input: { icon: '👤', label: 'Thông tin khách hàng cung cấp' },
  unknown: { icon: '❓', label: 'Chưa xác định' },
};

const EXEC_MODE_STAFF: Record<string, string> = {
  draft_only: 'Chỉ tạo bản nháp, chưa xử lý thật',
  read_only: 'Chỉ tra cứu, không thay đổi dữ liệu',
  manual: 'Nhân viên cần xử lý thủ công',
};

const ACTION_STATUS_STAFF: Record<string, { icon: string; label: string; cls: string }> = {
  draft: { icon: '📝', label: 'Bản nháp', cls: 'badge-blue' },
  draft_ready: { icon: '📝', label: 'Nháp sẵn sàng', cls: 'badge-blue' },
  waiting_approval: { icon: '⏳', label: 'Đang chờ phê duyệt', cls: 'badge-amber' },
  manual_required: { icon: '🛠', label: 'Cần xử lý thủ công', cls: 'badge-purple' },
};

const MCP_TOOL_FRIENDLY: Record<string, string> = {
  create_force_success_draft: 'Tạo yêu cầu cập nhật giao dịch thành công',
  create_refund_draft: 'Tạo yêu cầu hoàn tiền',
  create_reconciliation_draft: 'Tạo phiếu đối soát',
  create_unlock_account_draft: 'Tạo yêu cầu mở khóa tài khoản',
  create_customer_response_draft: 'Soạn phản hồi cho khách hàng',
};

const WORKFLOW_FRIENDLY: Record<string, string> = {
  wallet_topup: 'Nạp tiền vào ví',
  train_ticket: 'Mua vé tàu',
  utility_bill: 'Thanh toán hóa đơn',
  fraud_account_lock: 'Khóa tài khoản (chống gian lận)',
  unknown: 'Chưa xác định',
};

const CLAIM_STATUS_BADGE: Record<string, { icon: string; label: string; cls: string }> = {
  matched: { icon: '✅', label: 'Khớp', cls: 'cv-badge-matched' },
  mismatched: { icon: '⚠️', label: 'Lệch', cls: 'cv-badge-mismatched' },
  not_verifiable: { icon: '❓', label: 'Không xác minh được', cls: 'cv-badge-unknown' },
  not_found: { icon: '🔍', label: 'Không tìm thấy', cls: 'cv-badge-not-found' },
  system_only: { icon: 'ℹ️', label: 'Chỉ có dữ liệu hệ thống', cls: 'cv-badge-system-only' },
};

const CLAIM_TYPE_LABELS: Record<string, string> = {
  transaction_id_claim: 'Mã giao dịch',
  user_identity_claim: 'Thông tin định danh',
  transaction_amount_claim: 'Số tiền giao dịch',
  wallet_balance_claim: 'Số dư ví khách phản ánh',
  payment_status_claim: 'Trạng thái thanh toán khách phản ánh',
  service_delivery_claim: 'Tình trạng dịch vụ khách phản ánh',
  provider_status_claim: 'Trạng thái provider',
  bank_status_claim: 'Trạng thái ngân hàng',
  refund_status_claim: 'Trạng thái hoàn tiền',
  account_status_claim: 'Trạng thái tài khoản',
  withdrawal_status_claim: 'Trạng thái rút tiền',
  customer_opinion_claim: 'Ý kiến khách hàng',
  time_claim: 'Thời gian khách phản ánh',
  unknown_claim: 'Thông tin khác',
};

/** Staff-friendly labels for trusted data keys */
const TRUSTED_DATA_LABELS: Record<string, string> = {
  account_status: 'Trạng thái tài khoản',
  withdrawal_enabled: 'Trạng thái rút tiền',
  lock_reason: 'Lý do khóa',
  risk_score: 'Điểm rủi ro',
  risk_level: 'Mức rủi ro',
  fraud_status: 'Trạng thái fraud',
  recommended_decision: 'Kết quả rà soát',
  action_amount: 'Số tiền xử lý',
  action_amount_source: 'Nguồn số tiền',
  transaction_id: 'Mã giao dịch',
  user_id: 'User ID',
  service_type: 'Loại dịch vụ',
  wallet_status: 'Trạng thái ví',
  has_user_debit: 'Đã trừ tiền user',
  has_credit_refund: 'Đã hoàn tiền',
  bank_amount: 'Số tiền bank',
  bank_status: 'Trạng thái bank',
};

/** Format a claim value for display, applying currency suffix only when appropriate. */
function formatClaimValue(value: string | number | null | undefined, unit: string | null | undefined): string {
  if (value == null) return 'Khách không cung cấp';
  if (unit === 'VND' && typeof value === 'number') {
    return `${value.toLocaleString('vi-VN')}đ`;
  }
  return String(value);
}

/** Boolean-as-int value maps for trusted data display */
const BOOLEAN_FIELD_LABELS: Record<string, Record<number, string>> = {
  withdrawal_enabled: { 0: 'bị chặn', 1: 'cho phép' },
  has_user_debit: { 0: 'chưa trừ', 1: 'đã trừ' },
  has_credit_refund: { 0: 'chưa hoàn', 1: 'đã hoàn' },
};

/** Format a trusted data value for display — handles boolean-as-int and currency. */
function formatTrustedValue(key: string, val: unknown): string {
  if (val == null) return '—';
  // Boolean-as-int fields → human readable
  if (typeof val === 'number' && key in BOOLEAN_FIELD_LABELS) {
    const map = BOOLEAN_FIELD_LABELS[key];
    return map[val] ?? String(val);
  }
  // Currency fields
  if (typeof val === 'number' && (key === 'action_amount' || key === 'bank_amount')) {
    return `${val.toLocaleString('vi-VN')}đ`;
  }
  if (typeof val === 'number') {
    return val.toLocaleString('vi-VN');
  }
  return String(val);
}


/* ─── Staff-Friendly Action Card ─── */
function StaffActionCard({ action }: { action: TicketAction }) {
  const [showTech, setShowTech] = useState(false);
  const statusDisplay = ACTION_STATUS_STAFF[action.status] || ACTION_STATUS_STAFF.manual_required;
  const toolFriendly = action.mcp_tool ? (MCP_TOOL_FRIENDLY[action.mcp_tool] || action.mcp_tool) : null;

  return (
    <div className="sf-action-card">
      {/* ── Header ── */}
      <div className="sf-action-header">
        <span className="sf-action-name">{action.action_name}</span>
        <span className={`badge ${statusDisplay.cls}`} style={{ fontSize: '0.68rem' }}>
          {statusDisplay.icon} {statusDisplay.label}
        </span>
      </div>

      {/* ── Staff-Friendly Summary ── */}
      <div className="sf-summary">
        {/* Việc cần làm */}
        <div className="sf-row">
          <span className="sf-label">📌 Việc cần làm</span>
          <span className="sf-value">{action.description || action.action_name}</span>
        </div>

        {/* Vì sao cần làm */}
        {action.reason && (
          <div className="sf-row">
            <span className="sf-label">💡 Vì sao cần làm</span>
            <span className="sf-value">{action.reason}</span>
          </div>
        )}

        {/* Công cụ hệ thống sẽ dùng */}
        {toolFriendly && (
          <div className="sf-row">
            <span className="sf-label">🔧 Công cụ hệ thống sẽ dùng</span>
            <span className="sf-value">{toolFriendly}</span>
          </div>
        )}

        {/* Cách thực hiện */}
        <div className="sf-row">
          <span className="sf-label">⚙️ Cách thực hiện</span>
          <span className="sf-value">{EXEC_MODE_STAFF[action.execution_mode] || action.execution_mode}</span>
        </div>

        {/* Cần phê duyệt */}
        <div className="sf-row">
          <span className="sf-label">🔑 Cần phê duyệt</span>
          <span className="sf-value">
            {action.requires_approval ? '🔒 Có — cần được phê duyệt trước khi xử lý' : '✅ Không cần phê duyệt'}
          </span>
        </div>

        {/* Nhân viên cần kiểm tra */}
        {action.preconditions.length > 0 && (
          <div className="sf-row">
            <span className="sf-label">🔍 Nhân viên cần kiểm tra</span>
            <ul className="sf-check-list">
              {action.preconditions.map((p, i) => <li key={i}>{p}</li>)}
            </ul>
          </div>
        )}

        {/* Kết quả sau khi duyệt */}
        {action.expected_result && (
          <div className="sf-row sf-result-row">
            <span className="sf-label">✅ Sau khi phê duyệt</span>
            <span className="sf-value">{action.expected_result}</span>
          </div>
        )}

        {/* Lưu ý an toàn */}
        {action.safety_notes.length > 0 && (
          <div className="sf-row sf-safety-row">
            <span className="sf-label">⚠️ Lưu ý an toàn</span>
            <ul className="sf-safety-list">
              {action.safety_notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          </div>
        )}

        {/* Hướng dẫn cho nhân viên */}
        {action.staff_instruction && (
          <div className="sf-row sf-instruction-row">
            <span className="sf-label">👷 Hướng dẫn cho nhân viên</span>
            <span className="sf-value">{action.staff_instruction}</span>
          </div>
        )}
      </div>

      {/* ── Technical Details (collapsed) ── */}
      <button className="sf-tech-toggle" onClick={() => setShowTech(!showTech)}>
        <span>🔬 Chi tiết kỹ thuật</span>
        <span className={`sf-chevron ${showTech ? 'sf-chevron-open' : ''}`}>▾</span>
      </button>
      {showTech && (
        <div className="sf-tech-body">
          <div className="sf-tech-row">
            <span className="sf-tech-label">action_type</span>
            <code className="sf-tech-value">{action.action_type}</code>
          </div>
          <div className="sf-tech-row">
            <span className="sf-tech-label">action_id</span>
            <code className="sf-tech-value">{action.action_id}</code>
          </div>
          {action.mcp_tool && (
            <div className="sf-tech-row">
              <span className="sf-tech-label">mcp_tool</span>
              <code className="sf-tech-value">{action.mcp_tool}</code>
            </div>
          )}
          {action.mcp_input && Object.keys(action.mcp_input).length > 0 && (
            <div className="sf-tech-row">
              <span className="sf-tech-label">mcp_input</span>
              <pre className="sf-tech-pre">{JSON.stringify(action.mcp_input, null, 2)}</pre>
            </div>
          )}
          {action.evidence_dependencies.length > 0 && (
            <div className="sf-tech-row">
              <span className="sf-tech-label">evidence_dependencies</span>
              <div className="sf-tech-tags">
                {action.evidence_dependencies.map((e, i) => <code key={i} className="sf-tech-tag">{e}</code>)}
              </div>
            </div>
          )}
          <div className="sf-tech-row">
            <span className="sf-tech-label">execution_mode</span>
            <code className="sf-tech-value">{action.execution_mode}</code>
          </div>
          <div className="sf-tech-row">
            <span className="sf-tech-label">risk_level</span>
            <code className="sf-tech-value">{action.risk_level}</code>
          </div>
          <div className="sf-tech-row">
            <span className="sf-tech-label">approval_status</span>
            <code className="sf-tech-value">{action.approval_status}</code>
          </div>
          <div className="sf-tech-row">
            <span className="sf-tech-label">status</span>
            <code className="sf-tech-value">{action.status}</code>
          </div>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   MAIN PANEL
   ═══════════════════════════════════════════════════════════════ */

export default function ResolutionTicketPanel({ ticket, aiResponse }: Props) {
  const [copied, setCopied] = useState(false);
  const [showAiDetail, setShowAiDetail] = useState(false);

  if (!ticket) {
    return (
      <div className="rt-panel">
        <div className="rt-header">
          <span className="rt-icon">🎫</span>
          <span className="rt-title">Phiếu xử lý</span>
        </div>
        <div className="rt-empty">
          <span className="rt-empty-icon">📋</span>
          <p>Chưa có phiếu xử lý.</p>
        </div>
      </div>
    );
  }

  const statusDisplay = RESOLUTION_STATUS[ticket.resolution_status] || RESOLUTION_STATUS.not_supported;
  const locDisplay = LOCATION_LABELS[ticket.problem_location] || { icon: '📍', label: ticket.problem_location };
  const workflowFriendly = WORKFLOW_FRIENDLY[ticket.ticket_type] || ticket.ticket_type;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(ticket.customer_reply_draft);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard not available */ }
  };

  const debug = aiResponse?.debug;
  const isLlm = debug?.generation_mode === 'llm';

  return (
    <div className="rt-panel">
      {/* ══ Header ══ */}
      <div className="rt-header">
        <div className="rt-header-left">
          <span className="rt-icon">🎫</span>
          <span className="rt-title">Phiếu xử lý</span>
        </div>
        <div className="rt-header-badges">
          <span className={`badge ${statusDisplay.cls}`}>
            {statusDisplay.icon} {statusDisplay.label}
          </span>
          {isLlm && (
            <span className="badge badge-cyan rt-llm-badge">
              ✦ AI · {debug?.model_used || ''}
            </span>
          )}
          {!isLlm && debug?.fallback_reason && (
            <span className="badge badge-amber rt-llm-badge">⚠ Fallback</span>
          )}
        </div>
      </div>

      {/* ══ Body ══ */}
      <div className="rt-body">
        {/* ── Ticket Meta ── */}
        <div className="rt-meta">
          <div className="rt-meta-item">
            <span className="rt-meta-label">Mã phiếu</span>
            <code className="rt-meta-value">{ticket.ticket_id}</code>
          </div>
          <div className="rt-meta-item">
            <span className="rt-meta-label">Loại giao dịch</span>
            <span className="rt-meta-value">{workflowFriendly}</span>
          </div>
        </div>

        {/* ── Issue Summary ── */}
        <div className="rt-section">
          <div className="rt-section-label">📋 Tóm tắt vấn đề</div>
          <p className="rt-section-text">{ticket.issue_summary}</p>
        </div>

        {/* ── Claim Verification — Kiểm tra thông tin khách cung cấp ── */}
        {ticket.claim_verification && ticket.claim_verification.claims.length > 0 && (() => {
          const cv = ticket.claim_verification;
          return (
            <div className="rt-section cv-section">
              <div className="rt-section-label">🔍 Kiểm tra thông tin khách cung cấp</div>

              {/* Yellow warning: customer detail mismatch */}
              {cv.has_customer_detail_mismatch && !cv.has_system_evidence_conflict && (
                <div className="cv-warning cv-warning-yellow" id="cv-customer-mismatch-warning">
                  <span className="cv-warn-icon">⚠️</span>
                  <span>Thông tin khách cung cấp có điểm lệch. Agent sẽ xử lý theo dữ liệu chuẩn của hệ thống.</span>
                </div>
              )}

              {/* Red warning: system evidence conflict */}
              {cv.has_system_evidence_conflict && (
                <div className="cv-warning cv-warning-red" id="cv-system-conflict-warning">
                  <span className="cv-warn-icon">🚨</span>
                  <span>Các nguồn dữ liệu hệ thống đang mâu thuẫn. Cần kiểm tra thủ công trước khi tạo action rủi ro.</span>
                </div>
              )}

              {/* Claim verification table */}
              <div className="cv-table-wrapper">
                <table className="cv-table">
                  <thead>
                    <tr>
                      <th>Thông tin khách cung cấp</th>
                      <th>Dữ liệu hệ thống ghi nhận</th>
                      <th>Kết quả kiểm tra</th>
                      <th>Nguồn dữ liệu chuẩn</th>
                      <th>Giải thích cho nhân viên</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cv.claims.map((claim, i) => {
                      const badge = CLAIM_STATUS_BADGE[claim.verification_status] || CLAIM_STATUS_BADGE.not_verifiable;
                      const label = CLAIM_TYPE_LABELS[claim.claim_type] || claim.claim_type;
                      return (
                        <tr key={i} className={`cv-table-row cv-table-row-${claim.verification_status}`}>
                          <td>
                            <div className="cv-cell-label">{label}</div>
                            <div className="cv-cell-value">{formatClaimValue(claim.customer_claimed_value, claim.unit)}</div>
                            {claim.raw_text && (
                              <div className="cv-cell-raw" title="Trích từ khiếu nại">&quot;{claim.raw_text}&quot;</div>
                            )}
                          </td>
                          <td>
                            <div className="cv-cell-value">
                              {claim.trusted_system_value != null
                                ? formatClaimValue(claim.trusted_system_value, claim.unit)
                                : <span className="cv-na">—</span>}
                            </div>
                          </td>
                          <td>
                            <span className={`cv-badge ${badge.cls}`}>
                              {badge.icon} {badge.label}
                            </span>
                          </td>
                          <td>
                            <span className="cv-source">{claim.trusted_source || '—'}</span>
                          </td>
                          <td>
                            <span className="cv-explanation">{claim.explanation}</span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Trusted data used for action */}
              {cv.trusted_data_used_for_action && Object.keys(cv.trusted_data_used_for_action).length > 0 && (
                <div className="cv-trusted-data">
                  <span className="cv-trusted-label">📊 Dữ liệu chuẩn sẽ dùng cho action:</span>
                  <div className="cv-trusted-items">
                    {Object.entries(cv.trusted_data_used_for_action).map(([key, val]) => (
                      <span key={key} className="cv-trusted-item">
                        <span className="cv-trusted-key">{TRUSTED_DATA_LABELS[key] || key}:</span>
                        <span className="cv-trusted-val">
                          {formatTrustedValue(key, val)}
                        </span>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Staff explanation */}
              {cv.staff_explanation && (
                <div className="cv-staff-explanation">
                  <span className="cv-staff-label">👷 Giải thích cho nhân viên:</span>
                  <p className="cv-staff-text">{cv.staff_explanation}</p>
                </div>
              )}
            </div>
          );
        })()}

        {/* ── Problem Location ── */}
        <div className="rt-section">
          <div className="rt-section-label">📍 Vấn đề nằm ở đâu</div>
          <div className="rt-location-row">
            <span className="rt-location-badge">
              {locDisplay.icon} {locDisplay.label}
            </span>
          </div>
          {ticket.problem_explanation && (
            <p className="rt-section-text rt-explanation">{ticket.problem_explanation}</p>
          )}
        </div>

        {/* ── Amount Verification — Dữ liệu chuẩn để xử lý ── */}
        {ticket.amount_verification && (ticket.amount_verification.trusted_amount != null || ticket.amount_verification.customer_claimed_amount != null) && (() => {
          const av = ticket.amount_verification;
          return (
            <div className={`rt-section amt-verify-section ${av.has_amount_mismatch ? 'amt-mismatch' : ''}`}>
              <div className="rt-section-label">💰 Dữ liệu chuẩn để xử lý</div>

              {av.has_amount_mismatch && (
                <div className="amt-mismatch-warning">
                  <span className="amt-warn-icon">⚠️</span>
                  <span>{av.mismatch_description}</span>
                </div>
              )}

              <div className="amt-rows">
                {av.customer_claimed_amount != null && (
                  <div className="amt-row">
                    <span className="amt-label">Số tiền khách khai</span>
                    <span className={`amt-value ${av.has_amount_mismatch ? 'amt-claimed' : ''}`}>
                      {formatCurrency(av.customer_claimed_amount)}
                      <span className="amt-tag amt-tag-ref">Tham khảo</span>
                    </span>
                  </div>
                )}
                {av.trusted_amount != null && (
                  <div className="amt-row">
                    <span className="amt-label">Số tiền hệ thống ghi nhận</span>
                    <span className="amt-value amt-trusted">
                      {formatCurrency(av.trusted_amount)}
                      <span className="amt-tag amt-tag-trusted">Nguồn chuẩn</span>
                    </span>
                  </div>
                )}
                {av.trusted_amount_source && (
                  <div className="amt-row">
                    <span className="amt-label">Nguồn dữ liệu</span>
                    <span className="amt-value">{av.trusted_amount_source}</span>
                  </div>
                )}
                {av.action_amount != null && (
                  <div className="amt-row amt-row-action">
                    <span className="amt-label">Số tiền sẽ dùng nếu tạo action</span>
                    <span className="amt-value amt-action">
                      {formatCurrency(av.action_amount)}
                    </span>
                  </div>
                )}
              </div>

              {!av.has_amount_mismatch && av.trusted_amount != null && (
                <div className="amt-match-ok">
                  ✅ Số tiền khớp — hệ thống và khách hàng khai cùng giá trị.
                </div>
              )}
            </div>
          );
        })()}

        {/* ── Recommended Actions (staff-friendly) ── */}
        {ticket.recommended_actions.length > 0 && (
          <div className="rt-section rt-actions-section">
            <div className="rt-section-label">⚡ Hành động đề xuất</div>
            <div className="rt-actions-table">
              {ticket.recommended_actions.map((action, i) => (
                <StaffActionCard key={i} action={action} />
              ))}
            </div>
          </div>
        )}

        {/* ── Evidence Checked / Missing ── */}
        <div className="rt-section">
          <div className="rt-section-label">📊 Dữ liệu đã kiểm tra</div>
          {ticket.evidence_checked.length > 0 && (
            <div className="rt-evidence-group">
              <span className="rt-evidence-heading">✅ Đã kiểm tra:</span>
              <div className="rt-tags">
                {ticket.evidence_checked.map((ev, i) => (
                  <span key={i} className="rt-tag rt-tag-checked">{ev}</span>
                ))}
              </div>
            </div>
          )}
          {ticket.missing_evidence.length > 0 && (
            <div className="rt-evidence-group">
              <span className="rt-evidence-heading">⚠ Còn thiếu:</span>
              <div className="rt-tags">
                {ticket.missing_evidence.map((ev, i) => (
                  <span key={i} className="rt-tag rt-tag-missing">{ev}</span>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* ── Staff Instruction ── */}
        <div className="rt-section rt-staff-section">
          <div className="rt-section-label">👷 Hướng dẫn cho nhân viên</div>
          <p className="rt-section-text">{ticket.staff_instruction}</p>
        </div>

        {/* ── Customer Reply Draft ── */}
        <div className="rt-section rt-reply-section">
          <div className="rt-reply-header">
            <span className="rt-section-label">💬 Nháp trả lời khách hàng</span>
            {ticket.customer_reply_draft && (
              <button className="rt-copy-btn" onClick={handleCopy} title="Copy câu trả lời">
                {copied ? '✓ Đã copy' : '📋 Copy'}
              </button>
            )}
          </div>
          <div className="rt-reply-box">{ticket.customer_reply_draft}</div>
        </div>

        {/* ── Safety Notes ── */}
        {ticket.safety_notes.length > 0 && (
          <div className="rt-section rt-safety-section">
            <div className="rt-section-label">⚠️ Lưu ý an toàn</div>
            <ul className="rt-safety-list">
              {ticket.safety_notes.map((note, i) => (
                <li key={i}>{note}</li>
              ))}
            </ul>
          </div>
        )}

        {/* ── Collapsible AI Detail ── */}
        {aiResponse && (
          <div className="rt-section rt-ai-detail">
            <button
              className="rt-ai-toggle"
              onClick={() => setShowAiDetail(!showAiDetail)}
            >
              <span>🤖 Chi tiết phân tích AI</span>
              <span className={`rt-chevron ${showAiDetail ? 'rt-chevron-open' : ''}`}>▾</span>
            </button>
            {showAiDetail && (
              <div className="rt-ai-body">
                <div className="rt-ai-row">
                  <span className="rt-detail-label">Phân tích nội bộ</span>
                  <p className="rt-detail-value">{aiResponse.internal_summary}</p>
                </div>
                <div className="rt-ai-row">
                  <span className="rt-detail-label">Bước tiếp theo</span>
                  <p className="rt-detail-value">{aiResponse.recommended_next_step}</p>
                </div>
                {aiResponse.evidence_supporting_problem_location?.length > 0 && (
                  <div className="rt-ai-row">
                    <span className="rt-detail-label">Dữ liệu hỗ trợ kết luận</span>
                    <div className="rt-tags">
                      {aiResponse.evidence_supporting_problem_location.map((ev, i) => (
                        <span key={i} className="rt-tag rt-tag-supporting">{ev}</span>
                      ))}
                    </div>
                  </div>
                )}
                {aiResponse.problem_location_confidence && (
                  <div className="rt-ai-row">
                    <span className="rt-detail-label">Độ tin cậy</span>
                    <span className="rt-detail-value">{aiResponse.problem_location_confidence}</span>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
