/**
 * ApproveConfirmModal — Confirmation modal for chat ticket actions.
 *
 * Title: "Bạn đang xác nhận bước xử lý nào?"
 * Content: ticket ID, action title, why recommended, effects,
 *          does-not-do, safety checklist, next status.
 * Confirm button uses approve_button_label from contract.
 * Staff must explicitly confirm before the action proceeds.
 */

import type { StaffAction } from '../api/chatTickets';

interface ApproveConfirmModalProps {
  ticketId: string;
  staffAction: StaffAction;
  currentStatus: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
}

export default function ApproveConfirmModal({
  ticketId,
  staffAction,
  currentStatus,
  onConfirm,
  onCancel,
  busy = false,
}: ApproveConfirmModalProps) {
  const a = staffAction;

  return (
    <div className="cht-modal-overlay" onClick={onCancel}>
      <div className="cht-modal" onClick={(e) => e.stopPropagation()}>
        <h2>🔒 Bạn đang xác nhận bước xử lý nào?</h2>

        {/* Ticket ID */}
        <div className="cht-modal-section">
          <div className="cht-modal-section-title">Ticket</div>
          <code className="cht-ticket-id">{ticketId}</code>
          {currentStatus && (
            <span className="badge badge-neutral" style={{ marginLeft: 8 }}>
              {currentStatus}
            </span>
          )}
        </div>

        {/* Action title */}
        <div className="cht-modal-section">
          <div className="cht-modal-section-title">Hành động</div>
          <div className="cht-modal-action-title">{a.action_title}</div>
          <div className="cht-modal-meta">
            {a.next_owner_team && (
              <span className="badge badge-blue">
                Team: {a.next_owner_team}
              </span>
            )}
            <span className={`badge ${a.approval_required ? 'badge-red' : 'badge-green'}`}>
              {a.approval_required ? 'Cần phê duyệt' : 'Không cần phê duyệt'}
            </span>
          </div>
        </div>

        {/* Why recommended */}
        {a.why_recommended && (
          <div className="cht-modal-section">
            <div className="cht-modal-section-title">Vì sao đề xuất bước này?</div>
            <p style={{ fontSize: '0.84rem', color: 'var(--text-secondary)', lineHeight: 1.5, margin: 0 }}>
              {a.why_recommended}
            </p>
          </div>
        )}

        {/* What will happen */}
        {a.approve_effect.length > 0 && (
          <div className="cht-modal-section">
            <div className="cht-modal-section-title">✅ Khi bấm nút này, hệ thống sẽ:</div>
            <ul className="cht-action-list cht-do">
              {a.approve_effect.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          </div>
        )}

        {/* What will NOT happen */}
        {a.approve_does_not_do.length > 0 && (
          <div className="cht-modal-section">
            <div className="cht-modal-section-title">🚫 Hệ thống sẽ KHÔNG:</div>
            <ul className="cht-action-list cht-dont">
              {a.approve_does_not_do.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Preconditions checked */}
        {a.preconditions_checked.length > 0 && (
          <div className="cht-modal-section">
            <div className="cht-modal-section-title">🛡️ Điều kiện đã kiểm tra</div>
            <ul className="cht-action-list cht-checklist">
              {a.preconditions_checked.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Missing preconditions */}
        <div className="cht-modal-section">
          <div className="cht-modal-section-title">⚠️ Còn thiếu / cần kiểm tra thêm</div>
          {a.missing_preconditions.length > 0 ? (
            <ul className="cht-action-list cht-dont">
              {a.missing_preconditions.map((p, i) => (
                <li key={i} className="cht-missing">{p}</li>
              ))}
            </ul>
          ) : (
            <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)', margin: 0 }}>
              Không có điều kiện thiếu quan trọng.
            </p>
          )}
        </div>

        {/* Safety warnings */}
        {a.safety_warnings.length > 0 && (
          <div className="cht-modal-section">
            <div className="alert alert-warning" style={{ fontSize: '0.82rem' }}>
              <span className="alert-icon">⚠️</span>
              <div>
                {a.safety_warnings.map((w, i) => (
                  <div key={i}>{w}</div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Next status */}
        {a.next_status_after_approve && (
          <div className="cht-modal-section">
            <div className="cht-modal-section-title">Trạng thái tiếp theo</div>
            <span className="badge badge-green">{a.next_status_after_approve}</span>
          </div>
        )}

        {/* Actions */}
        <div className="cht-modal-actions">
          <button className="btn btn-ghost" onClick={onCancel} disabled={busy}>
            Hủy
          </button>
          <button className="btn btn-success" onClick={onConfirm} disabled={busy}>
            {busy ? 'Đang xử lý…' : `✓ ${a.approve_button_label || 'Xác nhận'}`}
          </button>
        </div>
      </div>
    </div>
  );
}
