"""Supabase transaction repository — READ-ONLY."""

from __future__ import annotations

from fintech_agent.repositories.base import RecordNotFound
from fintech_agent.schemas.evidence import Transaction


class SupabaseTransactionRepository:
    """Supabase-backed transaction repository."""

    def __init__(self, client) -> None:
        self._client = client

    def get_by_id(self, transaction_id: str) -> Transaction:
        """Fetch a transaction by its ID."""
        resp = (
            self._client.table("transactions")
            .select("*")
            .eq("transaction_id", transaction_id)
            .execute()
        )
        if not resp.data:
            raise RecordNotFound("Transaction", "transaction_id", transaction_id)
        row = resp.data[0]
        return Transaction(
            transaction_id=row["transaction_id"],
            user_id=row["user_id"],
            service_type=row["service_type"],
            amount=row["amount"],
            status=row["status"],
            order_id=row.get("order_id"),
            bill_code=row.get("bill_code"),
            customer_code=row.get("customer_code"),
            provider_ref_id=row.get("provider_ref_id"),
            created_at=row.get("created_at"),
        )

    def get_by_user_id(self, user_id: str) -> list[Transaction]:
        """Fetch all transactions for a user."""
        resp = (
            self._client.table("transactions")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )
        return [
            Transaction(
                transaction_id=r["transaction_id"],
                user_id=r["user_id"],
                service_type=r["service_type"],
                amount=r["amount"],
                status=r["status"],
                order_id=r.get("order_id"),
                bill_code=r.get("bill_code"),
                customer_code=r.get("customer_code"),
                provider_ref_id=r.get("provider_ref_id"),
                created_at=r.get("created_at"),
            )
            for r in (resp.data or [])
        ]
