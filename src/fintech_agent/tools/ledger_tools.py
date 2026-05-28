"""Wallet ledger lookup tool — READ-ONLY.

The wallet ledger is the SOURCE OF TRUTH for money.
Refund amounts MUST come from here, never from complaint text.
"""

from __future__ import annotations

from dataclasses import dataclass

from fintech_agent.repositories import LedgerRepository, RecordNotFound
from fintech_agent.schemas.evidence import WalletLedger
from fintech_agent.tools.tool_errors import ToolDataNotFound, ToolTimeout

TOOL_NAME = "get_wallet_ledger"

# Mock: these transaction IDs simulate timeout
_TIMEOUT_IDS = frozenset({"TXN_TIMEOUT_001"})


@dataclass(frozen=True)
class LedgerResult:
    """Structured result from get_wallet_ledger."""

    success: bool
    ledger: WalletLedger | None = None
    error: str | None = None


def get_wallet_ledger(
    transaction_id: str,
    repo: LedgerRepository | None = None,
) -> LedgerResult:
    """Fetch wallet ledger for a transaction.

    Args:
        transaction_id: The transaction to look up.
        repo: Optional repository override.

    Returns:
        LedgerResult with the ledger or error.

    Raises:
        ToolTimeout: Simulated timeout.
        ToolDataNotFound: If no ledger record exists.
    """
    if transaction_id in _TIMEOUT_IDS:
        raise ToolTimeout(TOOL_NAME)

    _repo = repo or LedgerRepository()
    try:
        ledger = _repo.get_by_transaction_id(transaction_id)
        return LedgerResult(success=True, ledger=ledger)
    except RecordNotFound:
        raise ToolDataNotFound(TOOL_NAME, "transaction_id", transaction_id)
