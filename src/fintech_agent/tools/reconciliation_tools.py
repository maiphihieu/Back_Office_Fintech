"""Reconciliation status lookup tool — READ-ONLY."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fintech_agent.database.repository_factory import get_reconciliation_repo

if TYPE_CHECKING:
    from fintech_agent.repositories.reconciliation_repository import ReconciliationRepository
from fintech_agent.schemas.evidence import ReconciliationStatus

TOOL_NAME = "get_reconciliation_status"


@dataclass(frozen=True)
class ReconciliationResult:
    """Structured result from get_reconciliation_status."""

    success: bool
    reconciliation: ReconciliationStatus | None = None
    error: str | None = None


def get_reconciliation_status(
    transaction_id: str,
    repo: ReconciliationRepository | None = None,
) -> ReconciliationResult:
    """Fetch reconciliation status for a transaction.

    Unlike other tools, this returns success=True with reconciliation=None
    when no record exists (absence of reconciliation is normal).
    """
    _repo = repo or get_reconciliation_repo()
    result = _repo.get_by_transaction_id(transaction_id)
    return ReconciliationResult(success=True, reconciliation=result)
