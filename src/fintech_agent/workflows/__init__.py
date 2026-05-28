"""Workflow services — approval lifecycle and orchestration.

Usage:
    from fintech_agent.workflows import ApprovalService

    service = ApprovalService(audit=audit)
    service.register_pending(paused_state)
    final_state = service.approve_case("CASE_001", "ops_admin")
"""

from fintech_agent.workflows.approval_service import (
    AlreadyDecidedError,
    ApprovalError,
    ApprovalService,
    CaseNotFoundError,
)

__all__ = [
    "ApprovalService",
    "ApprovalError",
    "CaseNotFoundError",
    "AlreadyDecidedError",
]
