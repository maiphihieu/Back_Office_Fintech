"""Supabase account status repository — READ-ONLY."""

from __future__ import annotations

from fintech_agent.schemas.evidence import AccountStatus


class SupabaseAccountRepository:
    """Supabase-backed account status repository."""

    def __init__(self, client) -> None:
        self._client = client

    def get_account_status(self, user_id: str) -> AccountStatus | None:
        """Fetch account status for a user.

        Returns None if no account record exists.
        """
        resp = (
            self._client.table("accounts")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )
        if not resp.data:
            return None
        row = resp.data[0]
        return AccountStatus(
            user_id=row["user_id"],
            wallet_id=row.get("wallet_id"),
            account_status=row.get("account_status"),
            withdrawal_enabled=row.get("withdrawal_enabled"),
            lock_reason=row.get("lock_reason"),
            current_balance=row.get("current_balance"),
            locked_at=row.get("locked_at"),
        )
