"""Workflow services — approval lifecycle, orchestration, and registry.

Usage:
    from fintech_agent.workflows import ApprovalService
    from fintech_agent.workflows import get_registry, WorkflowSpec

    service = ApprovalService(audit=audit)
    service.register_pending(paused_state)
    final_state = service.approve_case("CASE_001", "ops_admin")

    # Registry access
    registry = get_registry()
    spec = registry.get("wallet_topup")
"""

from fintech_agent.workflows.approval_service import (
    AlreadyDecidedError,
    ApprovalError,
    ApprovalService,
    CaseNotFoundError,
)
from fintech_agent.workflows.workflow_registry import (
    WorkflowRegistry,
    WorkflowSpec,
    get_registry,
    reset_registry,
)
from fintech_agent.workflows.resolver_contract import (
    ResolverResult,
    RootCause,
)
from fintech_agent.workflows.generic_diagnosis import (
    DiagnosisResult,
    diagnose_case,
)

__all__ = [
    # Approval service
    "ApprovalService",
    "ApprovalError",
    "CaseNotFoundError",
    "AlreadyDecidedError",
    # Workflow registry
    "WorkflowRegistry",
    "WorkflowSpec",
    "get_registry",
    "reset_registry",
    # Resolver contract
    "ResolverResult",
    "RootCause",
    # Generic diagnosis
    "DiagnosisResult",
    "diagnose_case",
]
