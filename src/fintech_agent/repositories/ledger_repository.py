"""Wallet ledger repository — read-only, source of truth for money."""

from __future__ import annotations

from fintech_agent.repositories.base import BaseRepository, RecordNotFound
from fintech_agent.repositories.json_loader import load_mock_json
from fintech_agent.schemas.evidence import WalletLedger, WalletLedgerEntry


class LedgerRepository(BaseRepository):
    """Read-only repository for wallet ledger entries.

    The wallet ledger is the source of truth for money in the wallet.
    Refund amounts MUST come from here, never from complaint text.
    """

    def __init__(self) -> None:
        self._data: list[dict] | None = None

    def _load_data(self) -> list[dict]:
        if self._data is None:
            self._data = load_mock_json("mock_wallet_ledger.json")
        return self._data

    def get_by_transaction_id(self, transaction_id: str) -> WalletLedger:
        """Fetch wallet ledger for a transaction.

        Raises:
            RecordNotFound: If no ledger record matches.
        """
        for record in self._load_data():
            if record["transaction_id"] == transaction_id:
                entries = [WalletLedgerEntry(**e) for e in record.get("entries", [])]
                return WalletLedger(
                    transaction_id=record["transaction_id"],
                    user_id=record["user_id"],
                    status=record.get("status", "unknown"),
                    has_user_debit=record.get("has_user_debit", False),
                    debit_amount=record.get("debit_amount", 0),
                    has_credit_refund=record.get("has_credit_refund", False),
                    credit_refund_amount=record.get("credit_refund_amount", 0),
                    net_amount=record.get("net_amount", 0),
                    entries=entries,
                )
        raise RecordNotFound("WalletLedger", "transaction_id", transaction_id)
