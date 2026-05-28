"""Transaction repository — read-only access to transaction records."""

from __future__ import annotations

from fintech_agent.repositories.base import BaseRepository, RecordNotFound
from fintech_agent.repositories.json_loader import load_mock_json
from fintech_agent.schemas.evidence import Transaction


class TransactionRepository(BaseRepository):
    """Read-only repository for transaction records."""

    def __init__(self) -> None:
        self._data: list[dict] | None = None

    def _load_data(self) -> list[dict]:
        if self._data is None:
            self._data = load_mock_json("mock_transactions.json")
        return self._data

    def get_by_id(self, transaction_id: str) -> Transaction:
        """Fetch a transaction by its ID.

        Raises:
            RecordNotFound: If no transaction matches the given ID.
        """
        for record in self._load_data():
            if record["transaction_id"] == transaction_id:
                return Transaction(**record)
        raise RecordNotFound("Transaction", "transaction_id", transaction_id)

    def get_by_user_id(self, user_id: str) -> list[Transaction]:
        """Fetch all transactions for a user (for recent_actions lookup)."""
        return [
            Transaction(**r) for r in self._load_data() if r["user_id"] == user_id
        ]
