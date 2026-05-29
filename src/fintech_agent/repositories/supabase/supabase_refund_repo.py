"""Supabase refund status repository — READ-ONLY."""

from __future__ import annotations

from fintech_agent.repositories.base import RecordNotFound
from fintech_agent.schemas.evidence import RefundStatus


class SupabaseRefundRepository:
    """Supabase-backed refund status repository."""

    def __init__(self, client) -> None:
        self._client = client

    def get_by_transaction_id(self, transaction_id: str) -> RefundStatus:
        """Fetch refund status for a transaction."""
        resp = (
            self._client.table("refunds")
            .select("*")
            .eq("transaction_id", transaction_id)
            .execute()
        )
        if not resp.data:
            raise RecordNotFound("RefundStatus", "transaction_id", transaction_id)
        row = resp.data[0]
        return RefundStatus(
            transaction_id=row["transaction_id"],
            refund_status=row["status"],
            refund_amount=row.get("amount"),
            refund_id=row.get("refund_id"),
            requested_at=row.get("created_at"),
            executed_at=row.get("updated_at") if row["status"] == "executed" else None,
        )
