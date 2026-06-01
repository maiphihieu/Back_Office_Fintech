"""Node: conflict_detection — run conflict rules + claim verification on evidence.

Two distinct checks:
    1. System-vs-system conflict detection (blocking)
       → Routes to manual_review if conflicts found
    2. Customer claim verification (non-blocking)
       → Annotates ticket with claim verification results
       → Workflow continues using trusted system data
"""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.rules.claim_verifier import verify_all_claims
from fintech_agent.rules.conflict_rules import detect_all_conflicts
from fintech_agent.schemas.enums import AuditEventType, CaseStatus
from fintech_agent.schemas.evidence import EvidenceBundle


def detect_conflict(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Run conflict detection rules + claim verification on evidence bundle.

    1. System-vs-system conflict detection (blocking):
       Only conflicts between trusted system sources trigger manual_review.

    2. Customer claim verification (non-blocking):
       Customer claims are verified against system evidence.
       Mismatches are recorded but do NOT block the workflow.
       The agent continues using trusted system data.
    """
    evidence = state.get("evidence_bundle") or EvidenceBundle()
    user_id = state.get("user_id")
    case_id = state.get("case_id", "")
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    # ── 1. System-vs-system conflict detection (blocking) ────
    conflicts = detect_all_conflicts(
        evidence, case_user_id=user_id,
    )
    updated_evidence = evidence.model_copy(update={"conflicts": conflicts})
    has_conflict = len(conflicts) > 0

    # ── 2. Customer claim verification (non-blocking) ────────
    extracted_info = state.get("extracted_info")
    # Inject raw_complaint into extracted_info so claim synthesis can
    # detect fraud-specific keywords (e.g. "vô cớ", "rút tiền")
    raw_complaint = state.get("raw_complaint")
    if extracted_info is not None and raw_complaint:
        if hasattr(extracted_info, "model_dump"):
            ei_for_claims = extracted_info.model_dump(mode="json", exclude_none=True)
        elif isinstance(extracted_info, dict):
            ei_for_claims = dict(extracted_info)
        else:
            ei_for_claims = {}
        ei_for_claims["_raw_complaint"] = raw_complaint
    else:
        ei_for_claims = extracted_info
    claim_summary = verify_all_claims(ei_for_claims, evidence)

    next_status = CaseStatus.CONFLICT_DETECTED if has_conflict else CaseStatus.ROUTED

    # Audit: system conflicts
    if audit and has_conflict:
        ev = audit.log_event(
            case_id, AuditEventType.CONFLICT_DETECTED,
            details={
                "conflict_count": len(conflicts),
                "conflicts": [c.description for c in conflicts],
            },
            new_status=next_status.value,
        )
        audit_ids.append(ev.event_id)

    # Audit: claim verification results
    if audit and claim_summary.claims:
        ev = audit.log_event(
            case_id, AuditEventType.INFO_EXTRACTED,
            actor="claim_verifier",
            details={
                "claim_count": len(claim_summary.claims),
                "matched": claim_summary.matched_claims,
                "mismatched": claim_summary.mismatched_claims,
                "not_verifiable": claim_summary.not_verifiable_claims,
                "has_customer_detail_mismatch": claim_summary.has_customer_detail_mismatch,
                "has_system_evidence_conflict": claim_summary.has_system_evidence_conflict,
            },
        )
        audit_ids.append(ev.event_id)

    return {
        "evidence_bundle": updated_evidence,
        "conflicts": conflicts,
        "has_conflict": has_conflict,
        "claim_verification_summary": claim_summary,
        "status": next_status,
        "audit_event_ids": audit_ids,
    }
