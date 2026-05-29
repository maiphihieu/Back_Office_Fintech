#!/usr/bin/env python3
"""Run migration + seed SQL via direct PostgreSQL connection (psycopg2).

Usage:
    # Using DATABASE_URL from .env
    python scripts/run_migration_psql.py

    # Or pass DATABASE_URL directly
    DATABASE_URL="postgresql://..." python scripts/run_migration_psql.py

Requires psycopg2-binary: pip install psycopg2-binary
NEVER prints database password.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MIGRATION_FILE = Path(__file__).resolve().parent.parent / "supabase" / "migrations" / "001_initial_schema.sql"
SEED_FILE = Path(__file__).resolve().parent.parent / "supabase" / "seed" / "001_seed_mock_data.sql"


def run_sql_file(conn, filepath: Path, label: str) -> None:
    """Execute a SQL file against the connection."""
    sql = filepath.read_text(encoding="utf-8")
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  File: {filepath.name}")
    print(f"  Size: {len(sql)} bytes")
    print(f"{'='*60}")

    cur = conn.cursor()
    try:
        cur.execute(sql)
        conn.commit()
        print(f"  ✅ {label} executed successfully")
    except Exception as e:
        conn.rollback()
        print(f"  ❌ {label} failed: {e}")
        raise
    finally:
        cur.close()


def verify_data(conn) -> None:
    """Verify tables have data."""
    print(f"\n{'='*60}")
    print("  Verification")
    print(f"{'='*60}")

    cur = conn.cursor()
    tables = [
        "transactions", "wallet_ledger_entries", "refunds",
        "train_provider_statuses", "utility_provider_statuses",
        "reconciliation_cases", "cases", "approval_packets", "audit_events",
    ]
    for table in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            print(f"  ✅ {table}: {count} rows")
        except Exception as e:
            print(f"  ❌ {table}: {e}")
            conn.rollback()
    cur.close()


def main() -> None:
    db_url = os.environ.get("DATABASE_URL", "")

    if not db_url:
        print("❌ DATABASE_URL not set.")
        print()
        print("Set it in .env or as an environment variable:")
        print()
        print("  DATABASE_URL=postgresql://postgres.[ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres")
        print()
        print("Find it in Supabase Dashboard → Settings → Database → Connection string (URI)")
        sys.exit(1)

    # Mask the password in output
    safe_url = db_url
    if "@" in db_url:
        pre_at = db_url.split("@")[0]
        post_at = db_url.split("@")[1]
        if ":" in pre_at:
            parts = pre_at.rsplit(":", 1)
            safe_url = f"{parts[0]}:***@{post_at}"
    print(f"🔌 Connecting to: {safe_url}")

    try:
        import psycopg2
    except ImportError:
        print("❌ psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    try:
        conn = psycopg2.connect(db_url)
        print("✅ Connected to PostgreSQL")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)

    try:
        # Step 1: Migration
        if MIGRATION_FILE.exists():
            run_sql_file(conn, MIGRATION_FILE, "Migration: Create tables")
        else:
            print(f"⚠️  Migration file not found: {MIGRATION_FILE}")

        # Step 2: Seed
        if SEED_FILE.exists():
            run_sql_file(conn, SEED_FILE, "Seed: Insert mock data")
        else:
            print(f"⚠️  Seed file not found: {SEED_FILE}")

        # Step 3: Verify
        verify_data(conn)

        print("\n🎉 Migration complete!")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
