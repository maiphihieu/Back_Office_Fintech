"""LangGraph agent state — the single typed dict flowing through the graph.

All nodes read/write from this state. LangGraph manages the state
automatically between node invocations.
"""

from __future__ import annotations

from typing import Annotated, Any

from langgraph.graph import add_messages
from typing_extensions import TypedDict

from fintech_agent.schemas.actions import RecommendedAction
from fintech_agent.schemas.approval import ApprovalDecision, ApprovalPacket
from fintech_agent.schemas.case_state import ExtractedInfo
from fintech_agent.schemas.claim_verification import ClaimVerificationSummary
from fintech_agent.schemas.enums import ApprovalStatus, CaseStatus
from fintech_agent.schemas.evidence import EvidenceBundle, EvidenceConflict
from fintech_agent.schemas.resolution_ticket import ResolutionTicket
from fintech_agent.schemas.response_generation import GeneratedResponse


class AgentState(TypedDict, total=False):
    """Typed state for the fintech workflow agent graph.

    Fields are grouped by pipeline stage. All are optional (total=False)
    because the state is built up incrementally as nodes execute.
    """

    # ── Identity ─────────────────────────────────────────────
    case_id: str
    raw_complaint: str
    user_id: str
    ticket_id: str

    # ── Extraction (LLM) ────────────────────────────────────
    extracted_info: ExtractedInfo | None
    missing_info: list[str]

    # ── Evidence (tools) ─────────────────────────────────────
    evidence_bundle: EvidenceBundle | None
    tool_results: dict[str, Any]

    # ── Workflow routing ─────────────────────────────────────
    selected_workflow: str | None  # "train_ticket" | "utility_bill" | None

    # ── Conflict detection ───────────────────────────────────
    conflicts: list[EvidenceConflict]
    has_conflict: bool

    # ── Claim verification (non-blocking) ────────────────────
    claim_verification_summary: ClaimVerificationSummary | None

    # ── Rule decision ────────────────────────────────────────
    rule_decision: dict[str, Any] | None  # TrainDecision / UtilityDecision serialized
    recommended_action: RecommendedAction | None

    # ── Approval gate ────────────────────────────────────────
    approval_packet: ApprovalPacket | None
    approval_decision: ApprovalDecision | None
    approval_status: ApprovalStatus | None
    approval_required: bool

    # ── Draft output ─────────────────────────────────────────
    draft_output: dict[str, Any] | None  # serialized draft (refund/reconciliation/response)

    # ── LLM generated response ───────────────────────────────
    generated_response: GeneratedResponse | None

    # ── Resolution ticket (deterministic) ────────────────────────
    resolution_ticket: ResolutionTicket | None

    # ── Lifecycle ────────────────────────────────────────────
    status: CaseStatus
    errors: list[str]
    retry_count: int
    max_retries: int
    audit_event_ids: list[str]
    correlation_id: str | None
