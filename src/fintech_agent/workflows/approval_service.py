"""ApprovalService — manages the human approval lifecycle.

Architecture:
  1. Graph runs until approval_gate → sets WAITING_APPROVAL, creates ApprovalPacket
  2. Graph stops (returns to caller with WAITING_APPROVAL state)
  3. Caller stores the paused state via ApprovalService.register_pending()
  4. Human calls approve_case() or reject_case()
  5. ApprovalService resumes the remaining graph nodes (create_draft → audit_and_close)

This is a two-phase commit pattern:
  Phase 1: evidence + rules + recommendation → ApprovalPacket
  Phase 2: human decision → draft creation → close

Safety guarantees:
  - No draft is created before human approval
  - Approved state is immutable after decision
  - Cannot approve/reject unknown or already-decided cases
  - All decisions are audit logged
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.state import AgentState
from fintech_agent.nodes.audit_close import audit_and_close
from fintech_agent.nodes.draft_action import create_draft
from fintech_agent.schemas.approval import ApprovalDecision, ApprovalPacket
from fintech_agent.schemas.enums import ApprovalStatus, AuditEventType, CaseStatus


class ApprovalError(Exception):
    """Base error for approval operations."""


class CaseNotFoundError(ApprovalError):
    """Raised when a case_id is not found in pending approvals."""


class AlreadyDecidedError(ApprovalError):
    """Raised when trying to approve/reject a case that already has a decision."""


class ApprovalService:
    """In-memory approval service for MVP.

    Stores paused graph states and provides approve/reject operations.
    In production, this would be backed by a database.

    Args:
        audit: Shared AuditLogger for recording approval events.
    """

    def __init__(self, audit: AuditLogger | None = None) -> None:
        self._pending: dict[str, AgentState] = {}
        self._decided: dict[str, AgentState] = {}
        self._audit = audit

    # ─── Registration ──────────────────────────────────────

    def register_pending(self, state: AgentState) -> str:
        """Register a case that is waiting for approval.

        Args:
            state: The full graph state after Phase 1 (WAITING_APPROVAL).

        Returns:
            The case_id.

        Raises:
            ValueError: If state is not in WAITING_APPROVAL status.
        """
        case_id = state.get("case_id", "")
        if not case_id:
            raise ValueError("state must have a case_id")
        if state.get("status") != CaseStatus.WAITING_APPROVAL:
            raise ValueError(
                f"state must be WAITING_APPROVAL, got {state.get('status')}"
            )
        self._pending[case_id] = dict(state)  # shallow copy
        return case_id

    # ─── Decision ──────────────────────────────────────────

    def approve_case(
        self,
        case_id: str,
        approver: str,
        comment: str | None = None,
    ) -> AgentState:
        """Approve a pending case and resume graph execution.

        Creates the draft and closes the case.

        Args:
            case_id: The case to approve.
            approver: ID or name of the human approver.
            comment: Optional comment from the approver.

        Returns:
            Final state after draft creation and case closure.

        Raises:
            CaseNotFoundError: If case_id is not pending.
            AlreadyDecidedError: If case was already decided.
        """
        state = self._get_pending(case_id)

        # Create approval decision
        decision = ApprovalDecision(
            case_id=case_id,
            approver=approver,
            status=ApprovalStatus.APPROVED,
            comment=comment,
        )

        # Update state with approval
        state["approval_decision"] = decision
        state["approval_status"] = ApprovalStatus.APPROVED

        audit_ids: list[str] = list(state.get("audit_event_ids", []))
        if self._audit:
            ev = self._audit.log_event(
                case_id, AuditEventType.HUMAN_APPROVED,
                actor=f"human:{approver}",
                details={
                    "comment": comment or "",
                    "proposed_action": state.get("approval_packet").proposed_action.value
                    if state.get("approval_packet") else "unknown",
                },
                previous_status=CaseStatus.WAITING_APPROVAL.value,
                new_status=CaseStatus.DRAFT_CREATED.value,
            )
            audit_ids.append(ev.event_id)
        state["audit_event_ids"] = audit_ids

        # Resume Phase 2: create_draft → audit_and_close
        state = _merge_state(state, create_draft(state, audit=self._audit))
        state = _merge_state(state, audit_and_close(state, audit=self._audit))

        # Move from pending to decided
        del self._pending[case_id]
        self._decided[case_id] = state

        return state

    def reject_case(
        self,
        case_id: str,
        approver: str,
        reason: str,
    ) -> AgentState:
        """Reject a pending case.

        No draft is created. Case is closed with rejection.

        Args:
            case_id: The case to reject.
            approver: ID or name of the human reviewer.
            reason: Reason for rejection.

        Returns:
            Final state after rejection and case closure.

        Raises:
            CaseNotFoundError: If case_id is not pending.
            AlreadyDecidedError: If case was already decided.
        """
        state = self._get_pending(case_id)

        decision = ApprovalDecision(
            case_id=case_id,
            approver=approver,
            status=ApprovalStatus.REJECTED,
            comment=reason,
        )

        state["approval_decision"] = decision
        state["approval_status"] = ApprovalStatus.REJECTED
        state["draft_output"] = {
            "type": "rejected",
            "reason": reason,
            "status": "rejected",
        }

        audit_ids: list[str] = list(state.get("audit_event_ids", []))
        if self._audit:
            ev = self._audit.log_event(
                case_id, AuditEventType.HUMAN_REJECTED,
                actor=f"human:{approver}",
                details={"reason": reason},
                previous_status=CaseStatus.WAITING_APPROVAL.value,
                new_status=CaseStatus.CLOSED.value,
            )
            audit_ids.append(ev.event_id)
        state["audit_event_ids"] = audit_ids

        # Close the case (no draft created)
        state = _merge_state(state, audit_and_close(
            {**state, "status": CaseStatus.CLOSED},
            audit=self._audit,
        ))
        state["status"] = CaseStatus.CLOSED

        del self._pending[case_id]
        self._decided[case_id] = state

        return state

    # ─── Query ─────────────────────────────────────────────

    def get_pending_cases(self) -> list[str]:
        """Return list of case_ids waiting for approval."""
        return list(self._pending.keys())

    def get_pending_state(self, case_id: str) -> AgentState | None:
        """Return the paused state for a pending case, or None."""
        return self._pending.get(case_id)

    def get_approval_packet(self, case_id: str) -> ApprovalPacket | None:
        """Return the ApprovalPacket for a pending case."""
        state = self._pending.get(case_id)
        if state:
            return state.get("approval_packet")
        return None

    def get_decided_state(self, case_id: str) -> AgentState | None:
        """Return the final state for a decided case, or None."""
        return self._decided.get(case_id)

    def is_pending(self, case_id: str) -> bool:
        """Check if a case is waiting for approval."""
        return case_id in self._pending

    def is_decided(self, case_id: str) -> bool:
        """Check if a case has been decided."""
        return case_id in self._decided

    # ─── Internals ─────────────────────────────────────────

    def _get_pending(self, case_id: str) -> AgentState:
        """Get pending state or raise appropriate error."""
        if case_id in self._decided:
            raise AlreadyDecidedError(
                f"case {case_id} already decided "
                f"(status={self._decided[case_id].get('approval_status')})"
            )
        if case_id not in self._pending:
            raise CaseNotFoundError(f"case {case_id} not found in pending approvals")
        return self._pending[case_id]


def _merge_state(base: AgentState, updates: AgentState) -> AgentState:
    """Merge node return values into base state."""
    merged = dict(base)
    for key, value in updates.items():
        merged[key] = value
    return merged
