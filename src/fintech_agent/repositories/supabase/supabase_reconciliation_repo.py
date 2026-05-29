"""Supabase reconciliation status repository — READ-ONLY."""

from __future__ import annotations

from fintech_agent.schemas.evidence import ReconciliationStatus


class SupabaseReconciliationRepository:
    """Supabase-backed reconciliation status repository."""

    def __init__(self, client) -> None:
        self._client = client

    def get_by_transaction_id(self, transaction_id: str) -> ReconciliationStatus | None:
        """Fetch reconciliation status for a transaction.

        Returns None if no reconciliation record exists (normal for
        transactions without a mismatch).
        """
        resp = (
            self._client.table("reconciliation_cases")
            .select("*")
            .eq("transaction_id", transaction_id)
            .execute()
        )
        if not resp.data:
            return None
        row = resp.data[0]
        details = row.get("details") or {}
        return ReconciliationStatus(
            transaction_id=row["transaction_id"],
            status=row.get("status"),
            mismatch_type=row.get("mismatch_type"),
            ticket_id=row.get("reconciliation_id"),
            created_at=row.get("created_at"),
            bank_status=details.get("bank_status"),
            bank_amount=details.get("bank_amount"),
            money_received_in_master_wallet=details.get("money_received_in_master_wallet"),
            bank_ref_id=details.get("bank_ref_id"),
            note=details.get("note"),
        )
