"""Public-safe diagnosis mapper.

Converts internal transaction/case/resolution data into a STRUCTURED,
workflow-aware public-safe diagnosis. It produces *meaning*, never a final
customer sentence — the LLM response composer turns the diagnosis into the
actual reply.

The diagnosis is fully data-driven:
  - The workflow's evidence is reduced to a workflow-neutral "situation"
    (payment_pending / payment_ok_delivery_pending / payment_failed /
    completed / checking, or a status-field situation for fraud locks).
  - The situation is then framed using the workflow-scoped vocabulary in
    customer_response_policy.yaml (workflows.<wf>.diagnosis).

This guarantees a train_ticket case NEVER borrows wallet_topup wording:
the framing comes from the train_ticket policy block, not shared code.

NEVER exposes raw table names, internal IDs, fraud scores, approval packets,
or tool outputs.

build_public_safe_diagnosis(workflow, raw_evidence, rule_result, resolution_status)
returns:
{
    "workflow": "...",
    "case_status": "...",
    "what_was_checked": [],
    "confirmed_public_facts": [],
    "likely_issue_location": null,
    "customer_safe_cause": null,
    "next_step": null,
    "customer_action_needed": null,
    "confidence": "low | medium | high",
}
"""

from __future__ import annotations

import logging
from typing import Any

from fintech_agent.llm.message_analyzer import load_response_policy

logger = logging.getLogger(__name__)


# ─── Policy helpers ─────────────────────────────────────────────

def _get_workflow_policy(policy: dict | None, workflow: str) -> dict:
    """Get workflow-specific policy or empty dict."""
    if not policy:
        return {}
    return policy.get("workflows", {}).get(workflow, {})


def _get_diagnosis_config(policy: dict | None, workflow: str) -> dict:
    """Get the workflow-scoped diagnosis vocabulary, or the neutral default."""
    wf_policy = _get_workflow_policy(policy, workflow)
    diag = wf_policy.get("diagnosis")
    if diag:
        return diag
    # Neutral, workflow-agnostic default — no wallet/ticket-specific wording.
    return (policy or {}).get("default_diagnosis", {}) or {}


# ─── Situation derivation (workflow-neutral) ────────────────────

_PAYMENT_OK_TOKENS = ("success", "completed", "confirmed", "paid")
_PAYMENT_FAILED_TOKENS = ("failed", "error", "declined", "rejected")
_PAYMENT_PENDING_TOKENS = ("pending", "processing", "in_progress")


def _payment_signal(raw_evidence: dict) -> str:
    """Reduce all payment-side evidence to ok | failed | pending | unknown.

    Looks at bank_status, payment_status, and the Vietnamese transaction_status
    label produced by the resolver. Workflow-neutral.
    """
    candidates = [
        str(raw_evidence.get("bank_status", "")).lower(),
        str(raw_evidence.get("payment_status", "")).lower(),
    ]
    txn_label = str(raw_evidence.get("transaction_status", "")).lower()

    for c in candidates:
        if c in _PAYMENT_FAILED_TOKENS:
            return "failed"
    if "gặp lỗi" in txn_label:
        return "failed"

    for c in candidates:
        if c in _PAYMENT_OK_TOKENS:
            return "ok"
    if "đã hoàn thành" in txn_label:
        return "ok"

    for c in candidates:
        if c in _PAYMENT_PENDING_TOKENS:
            return "pending"
    if "đang xử lý" in txn_label or "đang kiểm tra" in txn_label:
        return "pending"

    return "unknown"


def _downstream_signal(raw_evidence: dict, diag_cfg: dict) -> str:
    """Reduce workflow-specific downstream evidence to complete | pending | none.

    The downstream keys/values are defined per workflow in policy config, so the
    SAME generic logic frames wallet credit, ticket issuing, provider bill, or
    payout — whichever applies to the active workflow.
    """
    pending_keys = diag_cfg.get("downstream_pending_keys", {}) or {}
    complete_keys = diag_cfg.get("downstream_complete_keys", {}) or {}

    saw_complete = False
    saw_pending = False

    for key, values in complete_keys.items():
        v = str(raw_evidence.get(key, "")).lower()
        if v and v in [str(x).lower() for x in values]:
            saw_complete = True

    for key, values in pending_keys.items():
        v = str(raw_evidence.get(key, "")).lower()
        if v and v in [str(x).lower() for x in values]:
            saw_pending = True

    if saw_complete:
        return "complete"
    if saw_pending:
        return "pending"
    return "none"


def _rule_action(rule_result: dict | Any) -> str:
    """Extract the deterministic rule-engine action value, if any."""
    if not rule_result:
        return ""
    if isinstance(rule_result, dict):
        action = rule_result.get("action") or rule_result.get("recommended_action")
    else:
        action = getattr(rule_result, "action", None) or getattr(
            rule_result, "recommended_action", None,
        )
    if action is None:
        return ""
    return str(getattr(action, "value", action)).lower()


def _derive_situation(
    raw_evidence: dict,
    diag_cfg: dict,
    rule_result: dict | Any = None,
) -> str:
    """Map evidence/rule-result → one situation key in diag_cfg['situations'].

    Precedence:
      1. rule_action_situations (deterministic rule-engine action) — preferred,
         since the rule engine is the safe source of truth (e.g. merchant
         settlement). The internal action name is never surfaced.
      2. status_field mode (e.g. fraud locks): a single status field via status_map.
      3. payment/delivery mode (default): combine payment + downstream signals.
    """
    situations = diag_cfg.get("situations", {}) or {}

    # ── rule-action mode (preferred when available) ──
    action_map = {
        str(k).lower(): v
        for k, v in (diag_cfg.get("rule_action_situations", {}) or {}).items()
    }
    if action_map:
        action = _rule_action(rule_result)
        if action and action in action_map:
            situation = action_map[action]
            if situation in situations:
                return situation

    # ── status_field mode (fraud_account_lock etc.) ──
    status_field = diag_cfg.get("status_field")
    if status_field:
        raw_val = str(raw_evidence.get(status_field, "")).lower()
        status_map = {
            str(k).lower(): v for k, v in (diag_cfg.get("status_map", {}) or {}).items()
        }
        situation = status_map.get(raw_val)
        if situation and situation in situations:
            return situation
        return "checking" if "checking" in situations else next(iter(situations), "checking")

    # ── payment/delivery mode ──
    payment = _payment_signal(raw_evidence)
    downstream = _downstream_signal(raw_evidence, diag_cfg)

    if payment == "failed":
        return "payment_failed"

    if payment == "ok":
        if downstream == "complete":
            return "completed"
        # payment ok + downstream pending OR unknown:
        # the customer is complaining the service wasn't delivered, so the
        # bottleneck is the downstream (delivery) step — framed per workflow.
        return "payment_ok_delivery_pending"

    if payment == "pending":
        return "payment_pending"

    return "checking"


# ─── Confidence ─────────────────────────────────────────────────

def _determine_confidence(
    raw_evidence: dict,
    payment: str,
    downstream: str,
) -> str:
    """Confidence reflects how much corroborating evidence we actually have."""
    if not raw_evidence:
        return "low"
    if payment != "unknown" and downstream in ("complete", "pending"):
        return "high"
    if payment != "unknown" or downstream in ("complete", "pending"):
        return "medium"
    if raw_evidence.get("transaction_status") or raw_evidence.get("account_status"):
        return "medium"
    return "low"


# ─── What-was-checked (data-driven from evidence focus + keys) ───

_EVIDENCE_CHECK_LABELS = {
    "transaction_status": "trạng thái giao dịch",
    "payment_status": "trạng thái thanh toán",
    "bank_status": "trạng thái phía ngân hàng",
    "amount": "số tiền giao dịch",
    "time": "thời gian giao dịch",
    "wallet_status": "trạng thái cập nhật ví",
    "ticket_status": "trạng thái phát hành vé",
    "provider_status": "trạng thái từ nhà cung cấp",
    "bill_status": "trạng thái hóa đơn",
    "payout_status": "trạng thái chuyển khoản payout",
    "settlement_status": "trạng thái đối soát",
    "account_status": "trạng thái tài khoản",
}


def _build_what_was_checked(raw_evidence: dict) -> list[str]:
    """List of human-readable checks, derived from which evidence keys exist."""
    checks: list[str] = []
    for key, label in _EVIDENCE_CHECK_LABELS.items():
        if raw_evidence.get(key):
            checks.append(label)
    if not checks and raw_evidence:
        checks.append("thông tin giao dịch")
    return checks


# ─── Next step / customer action (workflow templates, no wallet leak) ──

def _build_next_step(
    resolution_status: str,
    workflow: str,
    policy: dict | None,
) -> str:
    """Public-safe next step. Uses workflow status_template (already scoped)."""
    wf_policy = _get_workflow_policy(policy, workflow)
    if resolution_status == "resolved":
        return (
            wf_policy.get("status_template")
            or "bộ phận phụ trách đang kiểm tra và xử lý theo quy trình"
        )
    if resolution_status == "multiple_candidates":
        return "cần thêm thông tin để xác định chính xác giao dịch"
    if resolution_status == "need_more_info":
        return "cần bổ sung thêm thông tin để tiếp tục xử lý"
    if resolution_status == "no_match":
        return "chưa tìm thấy giao dịch phù hợp, vui lòng kiểm tra lại thông tin"
    return "bộ phận phụ trách kiểm tra và xử lý theo quy trình"


def _build_customer_action(
    resolution_status: str,
    missing_info: list[str],
    workflow: str,
    policy: dict | None,
) -> str:
    """Guidance on what the customer should/should not do."""
    wf_policy = _get_workflow_policy(policy, workflow)
    safety = (
        wf_policy.get("safety_reminder")
        or (policy or {}).get("global_safety_reminder", "")
    )

    if missing_info:
        safe_fields = wf_policy.get("safe_missing_info", [])
        filtered = [f for f in missing_info if f in safe_fields] if safe_fields else missing_info
        if filtered:
            items = ", ".join(filtered)
            return f"bạn có thể cung cấp thêm: {items}. {safety}".strip()

    if resolution_status in ("resolved", "no_match"):
        return f"không cần gửi thêm thông tin nhạy cảm. {safety}".strip()

    return safety or "không cần gửi PIN/OTP/mật khẩu hoặc thông tin thẻ đầy đủ"


# ─── Core: workflow-aware structured diagnosis ──────────────────

def build_public_safe_diagnosis(
    workflow: str,
    raw_evidence: dict | None,
    rule_result: dict | None = None,
    resolution_status: str = "",
    policy: dict | None = None,
    missing_info: list[str] | None = None,
) -> dict:
    """Build a structured, workflow-aware public-safe diagnosis.

    Returns *meaning* (structured fields), never a final customer sentence.
    The framing vocabulary is read from the workflow's policy config, so the
    diagnosis depends on both the active workflow AND the evidence.

    Args:
        workflow: Active workflow name (authoritative — drives the framing).
        raw_evidence: Internal-but-already-sanitized evidence dict.
        rule_result: Deterministic rule-engine output, if any (read-only).
        resolution_status: resolved | multiple_candidates | no_match | etc.
        policy: Loaded customer_response_policy (loaded if None).
        missing_info: Fields still needed (for customer_action_needed).

    Returns:
        Diagnosis dict with the documented shape.
    """
    if policy is None:
        policy = load_response_policy()
    raw_evidence = raw_evidence or {}
    missing_info = missing_info or []
    workflow = workflow or ""

    diag_cfg = _get_diagnosis_config(policy, workflow)
    situations = diag_cfg.get("situations", {}) or {}

    situation = _derive_situation(raw_evidence, diag_cfg, rule_result)
    sit_cfg = situations.get(situation, {}) or {}

    # Signals (also reused for confidence)
    payment = _payment_signal(raw_evidence)
    downstream = _downstream_signal(raw_evidence, diag_cfg)

    issue_location = sit_cfg.get("issue_location") or None
    customer_safe_cause = (sit_cfg.get("cause") or "").strip() or None
    confirmed_facts = list(sit_cfg.get("facts", []) or [])

    # what_was_checked: prefer evidence-derived labels; otherwise fall back to
    # the workflow's declared checks (e.g. merchant settlement cycle/payout/bank).
    what_was_checked = _build_what_was_checked(raw_evidence)
    if not what_was_checked:
        what_was_checked = list(diag_cfg.get("default_what_was_checked", []) or [])

    next_step = _build_next_step(resolution_status, workflow, policy)
    # A situation may carry its own public-safe customer action (e.g. verify
    # bank account via official channel); else use the generic builder.
    customer_action = (sit_cfg.get("customer_action") or "").strip() or _build_customer_action(
        resolution_status, missing_info, workflow, policy,
    )
    confidence = _determine_confidence(raw_evidence, payment, downstream)
    # Rule-driven diagnosis is deterministic → at least medium confidence.
    if _rule_action(rule_result) and confidence == "low":
        confidence = "medium"

    diagnosis = {
        "workflow": workflow or "unknown",
        "case_status": resolution_status or "unknown",
        "situation": situation,
        "what_was_checked": what_was_checked,
        "confirmed_public_facts": confirmed_facts,
        "likely_issue_location": issue_location,
        "customer_safe_cause": customer_safe_cause,
        "next_step": next_step,
        "customer_action_needed": customer_action,
        "confidence": confidence,
    }
    logger.info(
        "[Diagnosis] workflow=%s situation=%s payment=%s downstream=%s conf=%s",
        workflow or "unknown", situation, payment, downstream, confidence,
    )
    return diagnosis


# ─── Backward-compatible wrapper ────────────────────────────────

def _safe_transaction_summary(raw_evidence: dict) -> str:
    """Legacy what_we_know summary (kept for backward compatibility)."""
    status = str(raw_evidence.get("transaction_status", "")).strip()
    parts: list[str] = []
    if status:
        parts.append(f"Giao dịch {status}")
    else:
        parts.append("Chúng tôi đang kiểm tra thông tin giao dịch")
    amount = raw_evidence.get("amount")
    if amount:
        parts.append(f"số tiền {amount}")
    return ". ".join(parts) + "." if parts else ""


def to_public_safe_evidence(
    raw_evidence: dict,
    rule_result: dict | None,
    workflow: str,
    policy: dict | None = None,
    resolution_status: str = "",
    missing_info: list[str] | None = None,
) -> dict:
    """Backward-compatible entry point used by the customer-chat pipeline.

    Delegates to build_public_safe_diagnosis and adds legacy keys
    (what_we_know) for existing callers/tests.
    """
    if policy is None:
        policy = load_response_policy()

    diagnosis = build_public_safe_diagnosis(
        workflow=workflow,
        raw_evidence=raw_evidence or {},
        rule_result=rule_result,
        resolution_status=resolution_status,
        policy=policy,
        missing_info=missing_info or [],
    )

    # Merge with legacy field for older composer/template code paths.
    result = dict(diagnosis)
    result["what_we_know"] = _safe_transaction_summary(raw_evidence or {})
    # Normalize None → "" for callers that string-concatenate.
    for key in ("likely_issue_location", "customer_safe_cause", "next_step"):
        if result.get(key) is None:
            result[key] = ""
    return result
