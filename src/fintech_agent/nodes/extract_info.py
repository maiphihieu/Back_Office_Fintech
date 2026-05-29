"""Node: extract_info — extract structured info from complaint text.

Phase 2: Uses LLM extractor (OpenAI or mock/regex based on MOCK_LLM).
The extractor handles routing internally.

SAFETY:
  - This node ONLY extracts information.
  - It does NOT decide refund, approval, amount, or any action.
  - Rule engine (apply_rules node) is the sole decision maker.
  - wallet_ledger is the sole source of truth for refund amount.
"""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.config import Settings, get_settings
from fintech_agent.graph.state import AgentState
from fintech_agent.llm import extract_complaint_info
from fintech_agent.schemas.enums import AuditEventType, CaseStatus


def extract_info(
    state: AgentState,
    audit: AuditLogger | None = None,
    settings: Settings | None = None,
) -> AgentState:
    """Extract transaction_id, user_id, service_type from input.

    Uses the LLM extractor module which routes to OpenAI or regex
    based on the MOCK_LLM setting.

    Args:
        state: Current agent state.
        audit: Optional audit logger.
        settings: Optional settings override (for testing).

    Returns:
        Updated state dict with extracted_info and related fields.
    """
    if settings is None:
        settings = get_settings()

    complaint = state.get("raw_complaint", "")
    user_id = state.get("user_id")

    # ── Call the extractor ────────────────────────────────────
    extracted = extract_complaint_info(
        complaint=complaint,
        settings=settings,
        user_id=user_id,
    )

    # ── Determine selected_workflow from service_type ─────────
    selected_workflow: str | None = None
    if extracted.service_type:
        st = extracted.service_type
        if st in ("train_ticket",):
            selected_workflow = "train_ticket"
        elif st in ("electric_bill", "water_bill"):
            selected_workflow = "utility_bill"
        elif st == "wallet_topup":
            selected_workflow = "wallet_topup"
        elif st == "account_security":
            selected_workflow = "fraud_account_lock"

    # ── Missing fields (use extractor's or compute) ───────────
    missing = list(extracted.missing_fields) if extracted.missing_fields else []
    if not missing:
        if not extracted.transaction_id:
            missing.append("transaction_id")
        if not extracted.user_id:
            missing.append("user_id")
        if not extracted.service_type:
            missing.append("service_type")

    next_status = CaseStatus.MISSING_INFO if missing else CaseStatus.FETCHING_EVIDENCE
    case_id = state.get("case_id", "")
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    # ── Audit logging ─────────────────────────────────────────
    if audit:
        # Log extraction failure if fallback was used
        if extracted.extraction_method == "fallback_regex":
            ev_fail = audit.log_event(
                case_id, AuditEventType.LLM_EXTRACTION_FAILED,
                details={
                    "reason": "openai_failed_or_invalid",
                    "fallback": "regex",
                },
            )
            audit_ids.append(ev_fail.event_id)

        # Always log the extraction result
        ev = audit.log_event(
            case_id, AuditEventType.INFO_EXTRACTED,
            details={
                "extraction_method": extracted.extraction_method or "unknown",
                "transaction_id": extracted.transaction_id or "MISSING",
                "service_type": str(extracted.service_type) if extracted.service_type else "UNKNOWN",
                "issue_type": str(extracted.issue_type) if extracted.issue_type else "UNKNOWN",
                "confidence": extracted.confidence,
                "missing_fields": missing,
            },
            new_status=next_status.value,
        )
        audit_ids.append(ev.event_id)

    return {
        "extracted_info": extracted,
        "missing_info": missing,
        "selected_workflow": selected_workflow,
        "status": next_status,
        "user_id": extracted.user_id or state.get("user_id", ""),
        "audit_event_ids": audit_ids,
    }
