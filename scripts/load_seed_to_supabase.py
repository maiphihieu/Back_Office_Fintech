#!/usr/bin/env python3
"""Load seed data to Supabase by executing the seed SQL.

Usage:
    python scripts/load_seed_to_supabase.py

This script reads the seed SQL file and executes it via Supabase's RPC.
Alternatively, you can run the SQL directly in the Supabase SQL Editor.

See docs/supabase_setup.md for detailed instructions.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

SEED_FILE = Path(__file__).resolve().parent.parent / "supabase" / "seed" / "001_seed_mock_data.sql"


def main() -> None:
    from fintech_agent.config import get_settings

    settings = get_settings()

    if not settings.supabase_enabled:
        print("⚠️  SUPABASE_ENABLED is false.")
        print("Set SUPABASE_ENABLED=true in .env first.")
        return

    if not SEED_FILE.exists():
        print(f"❌ Seed file not found: {SEED_FILE}")
        sys.exit(1)

    sql = SEED_FILE.read_text(encoding="utf-8")

    print(f"📄 Loading seed from: {SEED_FILE.name}")
    print(f"   SQL size: {len(sql)} bytes")

    try:
        from fintech_agent.database.supabase_client import get_supabase_client

        client = get_supabase_client(settings)

        # Execute via Supabase rpc or raw SQL
        # Note: Supabase Python SDK doesn't have a raw SQL execute.
        # Use the SQL Editor in the Supabase Dashboard instead.
        print()
        print("=" * 60)
        print("⚠️  The Supabase Python SDK does not support raw SQL execution.")
        print("Please run the seed SQL manually:")
        print()
        print("Option 1: Supabase Dashboard → SQL Editor")
        print(f"  Copy contents of: {SEED_FILE}")
        print()
        print("Option 2: psql (if you have direct DB access)")
        print(f"  psql <connection_string> -f {SEED_FILE}")
        print("=" * 60)

        # Verify if data already exists
        resp = client.table("transactions").select("transaction_id", count="exact").execute()
        count = resp.count if hasattr(resp, "count") and resp.count is not None else len(resp.data or [])
        if count > 0:
            print(f"\n✅ transactions table already has {count} rows (seed may already be applied)")
        else:
            print("\n⚠️  transactions table is empty — seed not yet applied")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
