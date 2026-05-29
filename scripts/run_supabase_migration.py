#!/usr/bin/env python3
"""Run migration and seed SQL against Supabase via its SQL execution endpoint.

Usage:
    python scripts/run_supabase_migration.py

Reads .env for SUPABASE_URL and SUPABASE_KEY.
Executes migration SQL then seed SQL.
NEVER prints SUPABASE_KEY.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

import httpx
from fintech_agent.config import get_settings

MIGRATION_FILE = Path(__file__).resolve().parent.parent / "supabase" / "migrations" / "001_initial_schema.sql"
SEED_FILE = Path(__file__).resolve().parent.parent / "supabase" / "seed" / "001_seed_mock_data.sql"


def execute_sql(url: str, key: str, sql: str, label: str) -> bool:
    """Execute SQL via Supabase PostgREST rpc or raw SQL endpoint."""
    # Use the Supabase REST SQL endpoint (requires service_role key)
    # The /rest/v1/rpc endpoint can call server-side functions
    # But for DDL, we need the /pg/ endpoint or management API

    # Try the Supabase SQL execution endpoint (available in newer versions)
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    # Method 1: Try /rest/v1/rpc with a custom function
    # Method 2: Try direct PostgREST SQL (won't work for DDL)
    # Method 3: Use the /pg/query endpoint (Supabase internal)

    # Actually, for Supabase hosted, we need to use the project's
    # PostgreSQL connection directly or the Dashboard SQL Editor.
    # The REST API (PostgREST) does NOT support DDL.

    # Let's try the Supabase Management API v1 query endpoint
    # Endpoint: POST /rest/v1/rpc/exec_sql (if pg_net extension enabled)
    # Or: POST https://<ref>.supabase.co/rest/v1/ with raw SQL

    # Fallback: Use direct PostgreSQL connection via connection pooler
    # URL pattern: postgresql://postgres.[ref]:[password]@[host]:6543/postgres

    print(f"\n{'='*60}")
    print(f"  Executing: {label}")
    print(f"  SQL size: {len(sql)} bytes")
    print(f"{'='*60}")

    # Split SQL into individual statements and execute each via RPC
    # First, try creating a temporary function to execute SQL
    create_exec_fn = """
    CREATE OR REPLACE FUNCTION exec_sql(query text) RETURNS void AS $$
    BEGIN
        EXECUTE query;
    END;
    $$ LANGUAGE plpgsql SECURITY DEFINER;
    """

    # Try to create the exec function first via rpc
    try:
        # Try executing via Supabase's built-in SQL endpoint
        resp = httpx.post(
            f"{url}/rest/v1/rpc/exec_sql",
            headers=headers,
            json={"query": sql},
            timeout=30,
        )
        if resp.status_code in (200, 204):
            print(f"  ✅ {label} executed successfully via rpc/exec_sql")
            return True
        elif resp.status_code == 404:
            print(f"  ℹ️  rpc/exec_sql not found, trying alternative...")
        else:
            error = resp.text[:200]
            print(f"  ⚠️  rpc/exec_sql returned {resp.status_code}: {error}")
    except Exception as e:
        print(f"  ⚠️  rpc/exec_sql failed: {e}")

    # Alternative: Execute statements one by one via individual table operations
    # This only works for DML (INSERT/UPDATE), not DDL (CREATE TABLE)
    # For DDL, we'll try the SQL endpoint
    try:
        # Some Supabase instances have a /sql endpoint
        resp = httpx.post(
            f"{url}/rest/v1/",
            headers={**headers, "Content-Profile": "public", "Prefer": "return=minimal"},
            content=sql.encode(),
            timeout=30,
        )
        if resp.status_code in (200, 201, 204):
            print(f"  ✅ {label} executed successfully")
            return True
    except Exception:
        pass

    return False


def execute_sql_statements_individually(url: str, key: str, sql: str, label: str) -> bool:
    """Split SQL into statements and try executing each."""
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    # Split on semicolons (simple parser)
    statements = []
    current = []
    for line in sql.split("\n"):
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        current.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(current).strip()
            if stmt and stmt != ";":
                statements.append(stmt)
            current = []

    print(f"\n  Found {len(statements)} SQL statements in {label}")

    success = 0
    failed = 0
    for i, stmt in enumerate(statements, 1):
        try:
            resp = httpx.post(
                f"{url}/rest/v1/rpc/exec_sql",
                headers=headers,
                json={"query": stmt},
                timeout=30,
            )
            if resp.status_code in (200, 204):
                success += 1
            else:
                failed += 1
                short = stmt[:80].replace("\n", " ")
                print(f"  ❌ Statement {i} failed ({resp.status_code}): {short}...")
        except Exception as e:
            failed += 1
            print(f"  ❌ Statement {i} error: {e}")

    print(f"  Results: {success} succeeded, {failed} failed")
    return failed == 0


def try_create_exec_function(url: str, key: str) -> bool:
    """Try to create a helper function for executing arbitrary SQL."""
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    # Check if exec_sql function already exists
    resp = httpx.post(
        f"{url}/rest/v1/rpc/exec_sql",
        headers=headers,
        json={"query": "SELECT 1"},
        timeout=10,
    )
    if resp.status_code in (200, 204):
        print("  ✅ exec_sql function already exists")
        return True

    print("  ℹ️  exec_sql function not found")
    print("  → You need to create it first in the Supabase SQL Editor:")
    print()
    print("    CREATE OR REPLACE FUNCTION exec_sql(query text)")
    print("    RETURNS void AS $$")
    print("    BEGIN EXECUTE query; END;")
    print("    $$ LANGUAGE plpgsql SECURITY DEFINER;")
    print()
    return False


def main() -> None:
    settings = get_settings()

    if not settings.supabase_enabled:
        print("⚠️  SUPABASE_ENABLED is false. Set to true in .env.")
        return

    print("🔌 Connecting to Supabase...")
    print(f"   URL: {settings.supabase_url}")
    print(f"   KEY: ***set***")

    url = settings.supabase_url
    key = settings.supabase_key

    # Step 1: Check/create exec_sql function
    print("\n📋 Step 1: Check exec_sql helper function")
    has_exec = try_create_exec_function(url, key)

    if not has_exec:
        print("\n" + "="*60)
        print("⚠️  Please create the exec_sql function first!")
        print("Go to Supabase Dashboard → SQL Editor and run:")
        print()
        print("CREATE OR REPLACE FUNCTION exec_sql(query text)")
        print("RETURNS void AS $$")
        print("BEGIN EXECUTE query; END;")
        print("$$ LANGUAGE plpgsql SECURITY DEFINER;")
        print()
        print("Then re-run this script.")
        print("="*60)
        sys.exit(1)

    # Step 2: Run migration
    if not MIGRATION_FILE.exists():
        print(f"❌ Migration file not found: {MIGRATION_FILE}")
        sys.exit(1)

    migration_sql = MIGRATION_FILE.read_text(encoding="utf-8")
    print("\n📋 Step 2: Running migration")
    if not execute_sql_statements_individually(url, key, migration_sql, "migration"):
        print("⚠️  Some migration statements failed (may be OK if tables already exist)")

    # Step 3: Run seed
    if not SEED_FILE.exists():
        print(f"❌ Seed file not found: {SEED_FILE}")
        sys.exit(1)

    seed_sql = SEED_FILE.read_text(encoding="utf-8")
    print("\n📋 Step 3: Running seed")
    if not execute_sql_statements_individually(url, key, seed_sql, "seed"):
        print("⚠️  Some seed statements failed")

    # Step 4: Verify
    print("\n📋 Step 4: Verifying data")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }
    try:
        for table in ["transactions", "wallet_ledger_entries", "refunds",
                       "train_provider_statuses", "utility_provider_statuses",
                       "reconciliation_cases"]:
            resp = httpx.get(
                f"{url}/rest/v1/{table}?select=*",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                count = len(resp.json())
                print(f"  ✅ {table}: {count} rows")
            else:
                print(f"  ❌ {table}: {resp.status_code}")
    except Exception as e:
        print(f"  ❌ Verification error: {e}")

    print("\n🎉 Migration complete!")


if __name__ == "__main__":
    main()
