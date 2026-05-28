"""Reconciliation status repository."""

from __future__ import annotations

from fintech_agent.repositories.base import BaseRepository, RecordNotFound
from fintech_agent.repositories.json_loader import load_mock_json
from fintech_agent.schemas.evidence import ReconciliationStatus


class ReconciliationRepository(BaseRepository):
    """Read-only repository for reconciliation records."""

    def __init__(self) -> None:
        self._data: list[dict] | None = None

    def _load_data(self) -> list[dict]:
        if self._data is None:
            self._data = load_mock_json("mock_reconciliation_status.json")
        return self._data

    def get_by_transaction_id(self, transaction_id: str) -> ReconciliationStatus | None:
        """Fetch reconciliation status for a transaction.

        Returns None if no reconciliation record exists (this is normal
        for transactions that don't have a mismatch).
        """
        for record in self._load_data():
            if record["transaction_id"] == transaction_id:
                return ReconciliationStatus(**record)
        return None
