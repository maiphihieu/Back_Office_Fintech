"""Node: generate_response — LLM summarization + resolution ticket after rule decision.

This node runs AFTER recommend_action and BEFORE approval_gate.
It calls the LLM response generator to produce a human-readable
summary, then builds a deterministic resolution ticket.

SAFETY INVARIANTS:
  - This node MUST NOT modify any decision fields:
    recommended_action, risk_level, approval_required, approval_status,
    selected_workflow, evidence_bundle, draft_output, rule_decision, status.
  - It ONLY adds generated_response and resolution_ticket fields to state.
  - If LLM fails, the graph continues with a safe fallback response.
  - Resolution ticket is ALWAYS built (even from fallback).
"""

from __future__ import annotations

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.llm.response_generator import generate_case_response
from fintech_agent.llm.ticket_builder import build_resolution_ticket
from fintech_agent.schemas.enums import AuditEventType


def generate_response(state: AgentState, audit: AuditLogger | None = None) -> AgentState:
    """Generate LLM response summary + resolution ticket and attach to state.

    Reads all safe fields from state, calls LLM (or fallback),
    then builds a deterministic resolution ticket.
    Returns ONLY the generated_response, resolution_ticket, and audit_event_ids.
    No decision fields are touched.
    """
    case_id = state.get("case_id", "")
    audit_ids: list[str] = list(state.get("audit_event_ids", []))

    # Build a flat dict for the response generator
    # (it expects dict, not TypedDict)
    state_dict = dict(state)

    # Serialize Pydantic models that might be in the state
    for key in ("evidence_bundle", "extracted_info", "recommended_action"):
        val = state_dict.get(key)
        if val is not None and hasattr(val, "model_dump"):
            state_dict[key] = val.model_dump(mode="json", exclude_none=True)

    # Map state keys to what response_generator expects
    if "evidence_bundle" in state_dict and "evidence" not in state_dict:
        state_dict["evidence"] = state_dict["evidence_bundle"]

    # Extract fields from recommended_action for context
    action = state.get("recommended_action")
    if action is not None:
        if hasattr(action, "action_type"):
            state_dict["recommended_action"] = action.action_type.value
        if hasattr(action, "risk_level"):
            state_dict["risk_level"] = (
                action.risk_level.value
                if hasattr(action.risk_level, "value")
                else action.risk_level
            )
        if hasattr(action, "approval_required"):
            state_dict["approval_required"] = action.approval_required
        if hasattr(action, "diagnosis"):
            state_dict["diagnosis"] = action.diagnosis

    # 1. Generate LLM response (or fallback)
    generated = generate_case_response(state_dict)

    # 2. Build deterministic resolution ticket from state + LLM response
    # Use original state (not state_dict) so Pydantic models are preserved
    ticket = build_resolution_ticket(dict(state), generated)

    # Audit log
    if audit:
        ev = audit.log_event(
            case_id=case_id,
            event_type=AuditEventType.RESPONSE_GENERATED,
            actor="llm_response_generator",
            details={
                "problem_location": generated.problem_location,
                "evidence_checked_count": len(generated.evidence_checked),
                "has_safety_notes": len(generated.safety_notes) > 0,
                "resolution_status": ticket.resolution_status,
                "actions_count": len(ticket.recommended_actions),
            },
        )
        audit_ids.append(ev.event_id)

    # Return ONLY the new fields — never overwrite decision fields
    return {
        "generated_response": generated,
        "resolution_ticket": ticket,
        "audit_event_ids": audit_ids,
    }
