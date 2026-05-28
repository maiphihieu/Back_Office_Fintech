"""Refund status lookup tool — READ-ONLY."""

from __future__ import annotations

from dataclasses import dataclass

from fintech_agent.repositories import RecordNotFound, RefundRepository
from fintech_agent.schemas.evidence import RefundStatus
from fintech_agent.tools.tool_errors import ToolDataNotFound

TOOL_NAME = "get_refund_status"


@dataclass(frozen=True)
class RefundStatusResult:
    """Structured result from get_refund_status."""

    success: bool
    refund_status: RefundStatus | None = None
    error: str | None = None


def get_refund_status(
    transaction_id: str,
    repo: RefundRepository | None = None,
) -> RefundStatusResult:
    """Fetch refund status for a transaction.

    Raises:
        ToolDataNotFound: If no refund record exists.
    """
    _repo = repo or RefundRepository()
    try:
        status = _repo.get_by_transaction_id(transaction_id)
        return RefundStatusResult(success=True, refund_status=status)
    except RecordNotFound:
        raise ToolDataNotFound(TOOL_NAME, "transaction_id", transaction_id)
