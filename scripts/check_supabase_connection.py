#!/usr/bin/env python3
"""Check Supabase connection and basic table access.

Usage:
    python scripts/check_supabase_connection.py

Reads config from .env. NEVER prints SUPABASE_KEY.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

from fintech_agent.config import get_settings


def main() -> None:
    settings = get_settings()

    print("=" * 60)
    print("Supabase Connection Check")
    print("=" * 60)

    print(f"SUPABASE_ENABLED: {settings.supabase_enabled}")
    print(f"SUPABASE_URL: {settings.supabase_url or '(not set)'}")
    print(f"SUPABASE_KEY: {'***set***' if settings.supabase_key else '(not set)'}")
    print(f"SUPABASE_SCHEMA: {settings.supabase_schema}")

    if not settings.supabase_enabled:
        print("\n⚠️  SUPABASE_ENABLED is false. Set to true in .env to enable.")
        return

    if not settings.supabase_url or not settings.supabase_key:
        print("\n❌ Missing SUPABASE_URL or SUPABASE_KEY in .env")
        sys.exit(1)

    print("\nConnecting to Supabase...")

    try:
        from fintech_agent.database.supabase_client import get_supabase_client

        client = get_supabase_client(settings)
        print("✅ Client created successfully")

        # Test query: count transactions
        resp = client.table("transactions").select("transaction_id", count="exact").execute()
        count = resp.count if hasattr(resp, "count") and resp.count is not None else len(resp.data or [])
        print(f"✅ transactions table: {count} rows")

        # Test query: count wallet_ledger_entries
        resp = client.table("wallet_ledger_entries").select("entry_id", count="exact").execute()
        count = resp.count if hasattr(resp, "count") and resp.count is not None else len(resp.data or [])
        print(f"✅ wallet_ledger_entries table: {count} rows")

        # Test query: count refunds
        resp = client.table("refunds").select("transaction_id", count="exact").execute()
        count = resp.count if hasattr(resp, "count") and resp.count is not None else len(resp.data or [])
        print(f"✅ refunds table: {count} rows")

        print("\n🎉 All checks passed! Supabase connection is working.")

    except Exception as e:
        print(f"\n❌ Connection failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
