"""Supabase case repository — persistence layer for case snapshots."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fintech_agent.repositories.base import RecordNotFound


class SupabaseCaseRepository:
    """Supabase-backed case persistence.

    Stores case result snapshots alongside the in-memory dict.
    This is a write-through cache — in-memory remains runtime source of truth.
    """

    def __init__(self, client) -> None:
        self._client = client

    def save(self, case_id: str, result_snapshot: dict) -> None:
        """Save or update a case snapshot."""
        record = {
            "case_id": case_id,
            "user_id": result_snapshot.get("user_id"),
            "transaction_id": result_snapshot.get("transaction_id"),
            "raw_complaint": result_snapshot.get("raw_complaint", ""),
            "service_type": result_snapshot.get("service_type"),
            "issue_type": result_snapshot.get("issue_type"),
            "selected_workflow": result_snapshot.get("selected_workflow"),
            "recommended_action": result_snapshot.get("recommended_action"),
            "risk_level": result_snapshot.get("risk_level"),
            "approval_required": result_snapshot.get("approval_required", False),
            "status": result_snapshot.get("status", "new"),
            "missing_fields": json.dumps(
                result_snapshot.get("missing_fields", [])
            ),
            "result_snapshot": json.dumps(result_snapshot),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._client.table("cases").upsert(
            record, on_conflict="case_id"
        ).execute()

    def get_by_id(self, case_id: str) -> dict:
        """Fetch a case snapshot by case_id."""
        resp = (
            self._client.table("cases")
            .select("*")
            .eq("case_id", case_id)
            .execute()
        )
        if not resp.data:
            raise RecordNotFound("Case", "case_id", case_id)
        return resp.data[0]

    def list_all(self) -> list[dict]:
        """List all case snapshots."""
        resp = (
            self._client.table("cases")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        return resp.data or []
