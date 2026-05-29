"""Supabase wallet ledger repository — READ-ONLY, source of truth for money.

Aggregates flat wallet_ledger_entries rows into the WalletLedger Pydantic model.
"""

from __future__ import annotations

from fintech_agent.repositories.base import RecordNotFound
from fintech_agent.schemas.evidence import WalletLedger, WalletLedgerEntry


class SupabaseLedgerRepository:
    """Supabase-backed wallet ledger repository."""

    def __init__(self, client) -> None:
        self._client = client

    def get_by_transaction_id(self, transaction_id: str) -> WalletLedger:
        """Fetch and aggregate wallet ledger for a transaction."""
        resp = (
            self._client.table("wallet_ledger_entries")
            .select("*")
            .eq("transaction_id", transaction_id)
            .order("created_at")
            .execute()
        )
        if not resp.data:
            raise RecordNotFound("WalletLedger", "transaction_id", transaction_id)

        rows = resp.data

        # Build entry list
        entries = [
            WalletLedgerEntry(
                entry_type=r["entry_type"],
                amount=r["amount"],
                balance_after=r.get("balance_after"),
                reason=r.get("reason"),
                created_at=r.get("created_at"),
            )
            for r in rows
        ]

        # Use the aggregate fields from the first row (they're per-transaction)
        first = rows[0]
        return WalletLedger(
            transaction_id=first["transaction_id"],
            user_id=first["user_id"],
            status=first.get("status", "unknown"),
            has_user_debit=first.get("has_user_debit", False),
            debit_amount=first.get("debit_amount", 0),
            has_credit_refund=first.get("has_credit_refund", False),
            credit_refund_amount=first.get("credit_refund_amount", 0),
            net_amount=first.get("net_amount", 0),
            entries=entries,
        )
