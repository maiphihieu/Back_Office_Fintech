"""Supabase account status repository — READ-ONLY."""

from __future__ import annotations

from fintech_agent.schemas.evidence import AccountStatus


class SupabaseAccountRepository:
    """Supabase-backed account status repository."""

    def __init__(self, client) -> None:
        self._client = client

    @staticmethod
    def _row_to_model(row: dict) -> AccountStatus:
        """Convert a Supabase row dict to AccountStatus model."""
        return AccountStatus(
            user_id=row["user_id"],
            wallet_id=row.get("wallet_id"),
            account_status=row.get("account_status"),
            withdrawal_enabled=row.get("withdrawal_enabled"),
            lock_reason=row.get("lock_reason"),
            current_balance=row.get("current_balance"),
            locked_at=row.get("locked_at"),
        )

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
        return self._row_to_model(resp.data[0])

    def get_by_phone(self, phone: str) -> AccountStatus | None:
        """Look up account by phone number. READ-ONLY.

        Returns None if no account with this phone exists.
        """
        resp = (
            self._client.table("accounts")
            .select("*")
            .eq("phone", phone)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return None
        return self._row_to_model(resp.data[0])

    def get_by_email(self, email: str) -> AccountStatus | None:
        """Look up account by email address. READ-ONLY.

        Returns None if no account with this email exists.
        """
        resp = (
            self._client.table("accounts")
            .select("*")
            .eq("email", email)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return None
        return self._row_to_model(resp.data[0])

    def get_by_wallet_id(self, wallet_id: str) -> AccountStatus | None:
        """Look up account by wallet_id. READ-ONLY.

        Returns None if no account with this wallet_id exists.
        """
        resp = (
            self._client.table("accounts")
            .select("*")
            .eq("wallet_id", wallet_id)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return None
        return self._row_to_model(resp.data[0])

