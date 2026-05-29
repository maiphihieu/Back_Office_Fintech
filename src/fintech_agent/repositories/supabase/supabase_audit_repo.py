"""Supabase audit event repository — persistence for audit trail."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4


class SupabaseAuditRepository:
    """Supabase-backed audit event persistence."""

    def __init__(self, client) -> None:
        self._client = client

    def insert(
        self,
        case_id: str,
        event_type: str,
        *,
        actor: str = "agent",
        details: dict | None = None,
        previous_status: str | None = None,
        new_status: str | None = None,
        correlation_id: str | None = None,
    ) -> dict:
        """Insert an audit event."""
        record = {
            "event_id": str(uuid4())[:12],
            "case_id": case_id,
            "actor": actor,
            "event_type": event_type,
            "previous_status": previous_status,
            "new_status": new_status,
            "details": details or {},
            "correlation_id": correlation_id,
            "created_at": datetime.now(UTC).isoformat(),
        }
        resp = self._client.table("audit_events").insert(record).execute()
        return resp.data[0] if resp.data else record

    def get_by_case(self, case_id: str) -> list[dict]:
        """Fetch all audit events for a case."""
        resp = (
            self._client.table("audit_events")
            .select("*")
            .eq("case_id", case_id)
            .order("created_at")
            .execute()
        )
        return resp.data or []
