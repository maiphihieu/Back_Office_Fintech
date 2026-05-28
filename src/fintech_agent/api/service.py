"""CaseService — orchestrates graph execution and case lifecycle.

This is the service layer between API endpoints and the graph/approval system.
API routes call CaseService methods; CaseService owns the shared state.
"""

from __future__ import annotations

from typing import Any

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.builder import compile_graph
from fintech_agent.graph.state import AgentState
from fintech_agent.schemas.enums import CaseStatus
from fintech_agent.workflows.approval_service import (
    AlreadyDecidedError,
    ApprovalService,
    CaseNotFoundError,
)


class CaseService:
    """In-memory case lifecycle manager for MVP.

    Manages:
      - Creating cases
      - Running the graph (Phase 1)
      - Storing results (pending, completed)
      - Delegating approve/reject to ApprovalService
    """

    def __init__(self) -> None:
        self.audit = AuditLogger()
        self.approval_service = ApprovalService(audit=self.audit)
        self._cases: dict[str, AgentState] = {}  # all cases by case_id

    # ─── Create & Run ──────────────────────────────────────

    def create_and_run(
        self,
        raw_complaint: str,
        user_id: str | None = None,
        transaction_id: str | None = None,
        service_type: str | None = None,
    ) -> AgentState:
        """Create a new case and run the graph (Phase 1).

        Returns the final state (may be WAITING_APPROVAL or CLOSED).
        """
        # Build initial state
        init: dict[str, Any] = {"raw_complaint": raw_complaint}
        if user_id:
            init["user_id"] = user_id

        # Inject pre-extracted info if provided
        if transaction_id or service_type:
            from fintech_agent.schemas.case_state import ExtractedInfo
            init["extracted_info"] = ExtractedInfo(
                transaction_id=transaction_id,
                service_type=service_type,
                user_id=user_id,
            )

        # Run graph Phase 1
        app = compile_graph(audit=self.audit)
        result = app.invoke(init)

        case_id = result.get("case_id", "")
        self._cases[case_id] = result

        # Auto-register pending approvals
        if result.get("status") == CaseStatus.WAITING_APPROVAL:
            self.approval_service.register_pending(result)

        return result

    def get_case(self, case_id: str) -> AgentState | None:
        """Get case state by ID."""
        # Check decided cases first (most up-to-date)
        decided = self.approval_service.get_decided_state(case_id)
        if decided:
            return decided
        return self._cases.get(case_id)

    def get_audit_trail(self, case_id: str) -> list[dict]:
        """Get audit events for a case as serializable dicts."""
        events = self.audit.get_events_by_case(case_id)
        return [
            {
                "event_id": e.event_id,
                "event_type": e.event_type.value,
                "actor": e.actor,
                "timestamp": e.timestamp.isoformat(),
                "previous_status": e.previous_status,
                "new_status": e.new_status,
                "details": e.details,
            }
            for e in events
        ]

    # ─── Approval ──────────────────────────────────────────

    def approve_case(
        self, case_id: str, approver: str, comment: str | None = None
    ) -> AgentState:
        """Approve a pending case (Phase 2)."""
        result = self.approval_service.approve_case(case_id, approver, comment)
        self._cases[case_id] = result
        return result

    def reject_case(
        self, case_id: str, approver: str, reason: str
    ) -> AgentState:
        """Reject a pending case."""
        result = self.approval_service.reject_case(case_id, approver, reason)
        self._cases[case_id] = result
        return result

    # ─── Query ─────────────────────────────────────────────

    def is_pending_approval(self, case_id: str) -> bool:
        return self.approval_service.is_pending(case_id)


# Singleton for the app lifecycle (MVP)
_service: CaseService | None = None


def get_case_service() -> CaseService:
    """Get or create the global CaseService singleton."""
    global _service
    if _service is None:
        _service = CaseService()
    return _service


def reset_case_service() -> None:
    """Reset the singleton (for testing)."""
    global _service
    _service = None
