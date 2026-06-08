"""Transaction lookup tool — READ-ONLY."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fintech_agent.database.repository_factory import get_transaction_repo

if TYPE_CHECKING:
    from fintech_agent.repositories.transaction_repository import TransactionRepository
from fintech_agent.repositories.base import RecordNotFound
from fintech_agent.schemas.evidence import Transaction
from fintech_agent.tools.tool_errors import ToolDataNotFound, ToolTimeout

TOOL_NAME = "get_transaction"

# Mock: these transaction IDs simulate timeout
_TIMEOUT_IDS = frozenset({"TXN_TIMEOUT_001"})


@dataclass(frozen=True)
class TransactionResult:
    """Structured result from get_transaction."""

    success: bool
    transaction: Transaction | None = None
    error: str | None = None


def get_transaction(
    transaction_id: str,
    repo: TransactionRepository | None = None,
) -> TransactionResult:
    """Fetch a transaction by ID.

    Args:
        transaction_id: The transaction to look up.
        repo: Optional repository override (for testing).

    Returns:
        TransactionResult with the transaction or error.

    Raises:
        ToolTimeout: If the transaction_id is in the timeout simulation set.
        ToolDataNotFound: If no transaction matches.
    """
    if transaction_id in _TIMEOUT_IDS:
        raise ToolTimeout(TOOL_NAME)

    _repo = repo or get_transaction_repo()
    try:
        txn = _repo.get_by_id(transaction_id)
        return TransactionResult(success=True, transaction=txn)
    except RecordNotFound:
        raise ToolDataNotFound(TOOL_NAME, "transaction_id", transaction_id)
