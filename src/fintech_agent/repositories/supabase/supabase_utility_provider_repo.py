"""Supabase utility provider status repository — READ-ONLY."""

from __future__ import annotations

from fintech_agent.repositories.base import RecordNotFound
from fintech_agent.schemas.evidence import UtilityProviderStatus


class SupabaseUtilityProviderRepository:
    """Supabase-backed utility bill provider status repository."""

    def __init__(self, client) -> None:
        self._client = client

    def get_by_ref_id(self, provider_ref_id: str) -> UtilityProviderStatus:
        """Fetch utility provider status by provider reference ID."""
        resp = (
            self._client.table("utility_provider_statuses")
            .select("*")
            .eq("provider_ref_id", provider_ref_id)
            .execute()
        )
        if not resp.data:
            raise RecordNotFound(
                "UtilityProviderStatus", "provider_ref_id", provider_ref_id
            )
        row = resp.data[0]
        return UtilityProviderStatus(
            provider_ref_id=row["provider_ref_id"],
            provider_status=row["status"],
            bill_status="paid" if row["status"] == "confirmed" else "unpaid",
            bill_code=row.get("bill_code"),
            customer_code=row.get("customer_code"),
            amount=row.get("amount"),
        )
