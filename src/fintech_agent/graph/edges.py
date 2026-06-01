"""Conditional edge functions for the LangGraph workflow.

Each function reads the current state and returns a node name string
to determine which node to route to next.
"""

from __future__ import annotations

from typing import Literal

from fintech_agent.graph.state import AgentState
from fintech_agent.schemas.enums import ApprovalStatus, CaseStatus


def after_extract_info(
    state: AgentState,
) -> Literal["missing_info_handler", "fetch_evidence"]:
    """After extraction: if info missing → handler, else → fetch evidence."""
    missing = state.get("missing_info", [])
    if missing:
        return "missing_info_handler"
    return "fetch_evidence"


def after_missing_info(
    state: AgentState,
) -> Literal["fetch_evidence", "audit_and_close"]:
    """After missing info handler: dead_letter → close, else → fetch."""
    if state.get("status") == CaseStatus.DEAD_LETTER:
        return "audit_and_close"
    return "fetch_evidence"


def after_fetch_evidence(
    state: AgentState,
) -> Literal["detect_conflict", "retry_or_dead_letter"]:
    """After fetch: if critical tools failed → retry, else → conflict check."""
    tool_results = state.get("tool_results", {})
    # Critical: transaction and wallet_ledger must succeed
    if tool_results.get("transaction") == "failed" and tool_results.get("wallet_ledger") == "failed":
        return "retry_or_dead_letter"
    return "detect_conflict"


def after_detect_conflict(
    state: AgentState,
) -> Literal["manual_review", "route_workflow"]:
    """After conflict detection: conflict → manual review, else → route."""
    if state.get("has_conflict", False):
        return "manual_review"
    return "route_workflow"


def after_route_workflow(
    state: AgentState,
) -> Literal["apply_rules", "manual_review"]:
    """After routing: if routed successfully → rules, else → manual review."""
    if state.get("status") == CaseStatus.MANUAL_REVIEW:
        return "manual_review"
    return "apply_rules"


def after_generate_response(
    state: AgentState,
) -> Literal["approval_gate", "create_draft"]:
    """After generate_response: if approval needed → gate, else → draft."""
    if state.get("status") == CaseStatus.WAITING_APPROVAL:
        return "approval_gate"
    return "create_draft"


def after_approval_gate(
    state: AgentState,
) -> Literal["create_draft", "audit_and_close"]:
    """After approval gate: decides next step based on approval status.

    - APPROVED → create_draft
    - NOT_REQUIRED → create_draft
    - PENDING → audit_and_close (stop graph; will be resumed by ApprovalService)
    - REJECTED → audit_and_close (case closed as rejected)
    - TIMEOUT → audit_and_close (case closed as timeout)
    """
    approval = state.get("approval_status")
    if approval in (ApprovalStatus.APPROVED, ApprovalStatus.NOT_REQUIRED):
        return "create_draft"
    # PENDING, REJECTED, TIMEOUT → stop the graph
    return "audit_and_close"


def after_retry(
    state: AgentState,
) -> Literal["fetch_evidence", "audit_and_close"]:
    """After retry decision: retry → fetch again, dead letter → close."""
    if state.get("status") == CaseStatus.DEAD_LETTER:
        return "audit_and_close"
    return "fetch_evidence"
