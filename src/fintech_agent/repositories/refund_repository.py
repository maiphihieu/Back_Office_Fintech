"""Refund status repository — source of truth for refund state."""

from __future__ import annotations

from fintech_agent.repositories.base import BaseRepository, RecordNotFound
from fintech_agent.repositories.json_loader import load_mock_json
from fintech_agent.schemas.evidence import RefundStatus


class RefundRepository(BaseRepository):
    """Read-only repository for refund records."""

    def __init__(self) -> None:
        self._data: list[dict] | None = None

    def _load_data(self) -> list[dict]:
        if self._data is None:
            self._data = load_mock_json("mock_refunds.json")
        return self._data

    def get_by_transaction_id(self, transaction_id: str) -> RefundStatus:
        """Fetch refund status for a transaction.

        Raises:
            RecordNotFound: If no refund record exists for this transaction.
        """
        for record in self._load_data():
            if record["transaction_id"] == transaction_id:
                return RefundStatus(**record)
        raise RecordNotFound("RefundStatus", "transaction_id", transaction_id)
