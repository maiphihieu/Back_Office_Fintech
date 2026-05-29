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
                # Extract bank fields from nested details if present
                details = record.get("details") or {}
                flat = {k: v for k, v in record.items() if k != "details"}
                for field in ("bank_status", "bank_amount",
                              "money_received_in_master_wallet",
                              "bank_ref_id", "note"):
                    if field not in flat and field in details:
                        flat[field] = details[field]
                return ReconciliationStatus(**flat)
        return None
