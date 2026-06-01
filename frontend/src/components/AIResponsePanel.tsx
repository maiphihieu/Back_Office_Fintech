/* ─── AI Response Panel — dynamic LLM summary for CS/Ops staff ─── */
/* Renders generated_response from API. No hard-coded per-workflow logic. */
/* problem_location is determined ONLY from structured evidence, not complaint. */

import { useState } from 'react';
import type { GeneratedResponse } from '../api/types';

interface Props {
  response: GeneratedResponse | null | undefined;
}

/** Map problem_location to human-readable + emoji */
const LOCATION_LABELS: Record<string, { icon: string; label: string }> = {
  wallet_system: { icon: '💰', label: 'Hệ thống ví' },
  bank: { icon: '🏦', label: 'Ngân hàng' },
  provider: { icon: '🏢', label: 'Nhà cung cấp' },
  fraud_system: { icon: '🚨', label: 'Hệ thống Fraud' },
  reconciliation: { icon: '🔄', label: 'Đối soát' },
  customer_input: { icon: '👤', label: 'Thông tin khách hàng' },
  unknown: { icon: '❓', label: 'Chưa xác định' },
};

function getLocationDisplay(loc: string) {
  return LOCATION_LABELS[loc] || { icon: '📍', label: loc };
}

/** Map confidence level to colored badge */
const CONFIDENCE_DISPLAY: Record<string, { color: string; label: string; icon: string }> = {
  high:    { color: 'badge-green',  label: 'Cao',          icon: '🟢' },
  medium:  { color: 'badge-amber',  label: 'Trung bình',   icon: '🟡' },
  low:     { color: 'badge-purple', label: 'Thấp',         icon: '🟠' },
  unknown: { color: 'badge-red',    label: 'Chưa xác định', icon: '🔴' },
};

function getConfidenceDisplay(conf: string) {
  return CONFIDENCE_DISPLAY[conf] || CONFIDENCE_DISPLAY['unknown'];
}

export default function AIResponsePanel({ response }: Props) {
  const [copied, setCopied] = useState(false);

  if (!response) {
    return (
      <div className="ai-panel">
        <div className="ai-panel-header">
          <span className="ai-panel-icon">🤖</span>
          <span className="ai-panel-title">AI Tổng hợp cho nhân viên</span>
        </div>
        <div className="ai-panel-empty">
          <span className="ai-panel-empty-icon">💬</span>
          <p>Chưa có tổng hợp AI.</p>
        </div>
      </div>
    );
  }

  const loc = getLocationDisplay(response.problem_location);
  const conf = getConfidenceDisplay(response.problem_location_confidence || 'unknown');

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(response.customer_reply_draft);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard not available */
    }
  };

  const debug = response.debug;
  const isLlm = debug?.generation_mode === 'llm';
  const supportingEvidence = response.evidence_supporting_problem_location || [];

  return (
    <div className="ai-panel">
      {/* Header */}
      <div className="ai-panel-header">
        <span className="ai-panel-icon">🤖</span>
        <span className="ai-panel-title">AI Tổng hợp cho nhân viên</span>
        <span className={`ai-mode-badge ${isLlm ? 'ai-mode-llm' : 'ai-mode-fallback'}`}>
          {isLlm ? `✦ LLM · ${debug?.model_used || ''}` : '⚠ Fallback'}
        </span>
      </div>

      {/* Fallback warning */}
      {!isLlm && debug?.fallback_reason && (
        <div className="ai-fallback-warning">
          ⚠ AI summary đang dùng fallback: {debug.fallback_reason}
        </div>
      )}

      {/* Scrollable body */}
      <div className="ai-panel-body">
        {/* Case Summary */}
        <div className="ai-section">
          <div className="ai-section-label">📋 Tóm tắt</div>
          <p className="ai-section-text">{response.case_summary}</p>
        </div>

        {/* Problem Location + Confidence */}
        <div className="ai-section">
          <div className="ai-section-label">📍 Vấn đề nằm ở đâu</div>
          <div className="ai-location-row">
            <div className="ai-location-badge">
              <span>{loc.icon}</span>
              <span>{loc.label}</span>
            </div>
            <span className={`badge ${conf.color} ai-confidence-badge`}>
              {conf.icon} {conf.label}
            </span>
          </div>
        </div>

        {/* Problem Explanation */}
        <div className="ai-section">
          <div className="ai-section-label">🔍 Vì sao</div>
          <p className="ai-section-text">{response.problem_explanation}</p>
        </div>

        {/* Supporting Evidence for problem_location */}
        {supportingEvidence.length > 0 && (
          <div className="ai-section">
            <div className="ai-section-label">🎯 Evidence dùng để kết luận</div>
            <div className="ai-evidence-list">
              {supportingEvidence.map((ev, i) => (
                <span key={i} className="ai-evidence-tag ai-evidence-supporting">{ev}</span>
              ))}
            </div>
          </div>
        )}

        {/* Evidence Checked */}
        {response.evidence_checked.length > 0 && (
          <div className="ai-section">
            <div className="ai-section-label">✅ Evidence đã kiểm tra</div>
            <div className="ai-evidence-list">
              {response.evidence_checked.map((ev, i) => (
                <span key={i} className="ai-evidence-tag">{ev}</span>
              ))}
            </div>
          </div>
        )}

        {/* Internal Summary */}
        <div className="ai-section ai-section-internal">
          <div className="ai-section-label">🔒 Nội bộ</div>
          <p className="ai-section-text">{response.internal_summary}</p>
        </div>

        {/* Recommended Next Step */}
        <div className="ai-section ai-section-action">
          <div className="ai-section-label">👉 Bước tiếp theo</div>
          <p className="ai-section-text">{response.recommended_next_step}</p>
        </div>

        {/* Customer Reply Draft */}
        <div className="ai-section ai-section-reply">
          <div className="ai-reply-header">
            <span className="ai-section-label">💬 Nháp trả lời khách</span>
            {response.customer_reply_draft && (
              <button
                className="ai-copy-btn"
                onClick={handleCopy}
                title="Copy câu trả lời"
              >
                {copied ? '✓ Đã copy' : '📋 Copy'}
              </button>
            )}
          </div>
          <div className="ai-reply-box">
            {response.customer_reply_draft}
          </div>
        </div>

        {/* Safety Notes */}
        {response.safety_notes.length > 0 && (
          <div className="ai-section ai-section-safety">
            <div className="ai-section-label">⚠️ Lưu ý an toàn</div>
            <ul className="ai-safety-list">
              {response.safety_notes.map((note, i) => (
                <li key={i}>{note}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
