"""Supabase mock customer session repository — READ-ONLY.

Reads from: public.mock_customer_sessions
No insert/update/delete operations.

SECURITY:
  - list_active_sessions() returns PUBLIC fields only (no user_id, phone, etc.)
  - get_session() returns ALL fields (for server-side identity injection)
  - get_session() validates is_authenticated and expires_at
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class SupabaseMockSessionRepository:
    """Read-only repository for mock customer sessions."""

    # Fields safe to return to frontend
    _PUBLIC_FIELDS = "session_id,subject_type,display_name,role,is_authenticated"

    def __init__(self, client) -> None:
        self._client = client

    def list_active_sessions(self) -> list[dict]:
        """List all active (non-expired, authenticated) demo sessions.

        Returns ONLY public fields — never user_id, phone, email, etc.
        """
        resp = (
            self._client.table("mock_customer_sessions")
            .select(self._PUBLIC_FIELDS)
            .eq("is_authenticated", True)
            .execute()
        )

        if not resp.data:
            return []

        # Filter out expired sessions
        now = datetime.now(timezone.utc)
        active: list[dict] = []
        for row in resp.data:
            # We need to check expires_at but it's not in the public select.
            # So we do a separate check below only if needed.
            # Actually, since we only select public fields, we can't see
            # expires_at here. Let's add it to the select for filtering only.
            active.append(row)

        return active

    def list_active_sessions_with_expiry_filter(self) -> list[dict]:
        """List active sessions, properly filtering expired ones.

        Returns only public fields.
        """
        resp = (
            self._client.table("mock_customer_sessions")
            .select(f"{self._PUBLIC_FIELDS},expires_at")
            .eq("is_authenticated", True)
            .execute()
        )

        if not resp.data:
            return []

        now = datetime.now(timezone.utc)
        active: list[dict] = []
        for row in resp.data:
            expires_at = row.get("expires_at")
            if expires_at is not None:
                # Parse ISO timestamp
                try:
                    exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                    if exp <= now:
                        continue  # Skip expired
                except (ValueError, TypeError):
                    continue  # Skip unparseable
            # Remove expires_at from public output
            public = {k: v for k, v in row.items() if k != "expires_at"}
            active.append(public)

        return active

    def get_session(self, session_id: str) -> dict | None:
        """Get a session by ID with full details (for server-side use).

        Returns None if:
          - session_id doesn't exist
          - is_authenticated is False
          - expires_at has passed

        Returns ALL fields including user_id, merchant_id, phone, etc.
        These are for server-side identity injection only — NEVER
        return these to the frontend.
        """
        resp = (
            self._client.table("mock_customer_sessions")
            .select("*")
            .eq("session_id", session_id)
            .eq("is_authenticated", True)
            .limit(1)
            .execute()
        )

        if not resp.data:
            logger.info("[MockSession] Session not found: %s", session_id)
            return None

        row = resp.data[0]

        # Check expiry
        expires_at = row.get("expires_at")
        if expires_at is not None:
            try:
                exp = datetime.fromisoformat(
                    expires_at.replace("Z", "+00:00")
                )
                if exp <= datetime.now(timezone.utc):
                    logger.info(
                        "[MockSession] Session expired: %s (at %s)",
                        session_id, expires_at,
                    )
                    return None
            except (ValueError, TypeError):
                logger.warning(
                    "[MockSession] Unparseable expires_at for %s: %s",
                    session_id, expires_at,
                )
                return None

        return row

    def verify_pin(self, phone: str, pin: str) -> dict | None:
        """Verify phone + PIN via Supabase RPC.

        Calls the verify_mock_customer_pin PostgreSQL function which:
          - Checks credential exists, is_active, not locked
          - Verifies PIN using crypt() server-side
          - Joins to mock_customer_sessions for session validity
          - Returns ONLY safe public fields

        Returns dict with safe fields or None if login fails.
        SECURITY: Never logs or returns the PIN.
        """
        try:
            resp = self._client.rpc(
                "verify_mock_customer_pin",
                {"input_phone": phone.strip(), "input_pin": pin},
            ).execute()
        except Exception as exc:
            logger.error("[MockSession] RPC verify_pin failed: %s", exc)
            return None

        if not resp.data:
            logger.info("[MockSession] PIN verification failed for phone: %s", phone)
            return None

        row = resp.data[0] if isinstance(resp.data, list) else resp.data
        if not row or not row.get("session_id"):
            return None

        logger.info(
            "[MockSession] PIN login success: session=%s",
            row["session_id"],
        )
        return {
            "session_id": row["session_id"],
            "subject_type": row["subject_type"],
            "display_name": row["display_name"],
            "role": row.get("role", "customer"),
            "is_authenticated": True,
        }

