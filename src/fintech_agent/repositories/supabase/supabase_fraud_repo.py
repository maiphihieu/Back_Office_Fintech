"""Supabase fraud case repository — READ-ONLY."""

from __future__ import annotations

from fintech_agent.schemas.evidence import FraudCase


class SupabaseFraudRepository:
    """Supabase-backed fraud case repository."""

    def __init__(self, client) -> None:
        self._client = client

    def get_fraud_case(self, user_id: str) -> FraudCase | None:
        """Fetch the latest fraud case for a user.

        Returns None if no fraud case exists for this user.
        Unpacks the `details` JSONB column into structured fields.
        """
        resp = (
            self._client.table("fraud_cases")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return None
        row = resp.data[0]
        details = row.get("details") or {}
        return FraudCase(
            fraud_case_id=row["fraud_case_id"],
            user_id=row["user_id"],
            risk_score=row.get("risk_score"),
            risk_level=row.get("risk_level"),
            fraud_status=row.get("fraud_status"),
            trigger_reason=row.get("trigger_reason"),
            signals=details.get("signals") or {},
            recent_transactions=details.get("recent_transactions") or [],
            device_events=details.get("device_events") or [],
            recommended_decision=details.get("recommended_decision"),
        )
