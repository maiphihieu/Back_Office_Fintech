"""Supabase train provider status repository — READ-ONLY."""

from __future__ import annotations

from fintech_agent.repositories.base import RecordNotFound
from fintech_agent.schemas.evidence import TrainProviderStatus


class SupabaseTrainProviderRepository:
    """Supabase-backed train provider status repository."""

    def __init__(self, client) -> None:
        self._client = client

    def get_by_ref_id(self, provider_ref_id: str) -> TrainProviderStatus:
        """Fetch train provider status by provider reference ID."""
        resp = (
            self._client.table("train_provider_statuses")
            .select("*")
            .eq("provider_ref_id", provider_ref_id)
            .execute()
        )
        if not resp.data:
            raise RecordNotFound(
                "TrainProviderStatus", "provider_ref_id", provider_ref_id
            )
        row = resp.data[0]
        return TrainProviderStatus(
            provider_ref_id=row["provider_ref_id"],
            booking_status=row["status"],
            ticket_code=row.get("ticket_code"),
            departure=None,  # Not stored in DB; kept for schema compat
        )
