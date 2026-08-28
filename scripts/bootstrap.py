#!/usr/bin/env python3
"""Bootstrap script: runs database migrations and verifies connectivity."""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from draftly.integrations.database.client import DatabaseClient

load_dotenv()

async def run_migrations() -> None:
    """Apply all SQL migration files in order."""
    migrations_dir = Path(__file__).parent.parent / "src" / "draftly" / "persistence" / "migrations"
    if not migrations_dir.exists():
        print(f"Migrations directory not found: {migrations_dir}")
        return

    migration_files = sorted(migrations_dir.glob("*.sql"))
    if not migration_files:
        print("No migration files found")
        return

    client = DatabaseClient()
    await client.start()
    try:
        for migration_file in migration_files:
            print(f"Applying migration: {migration_file.name}")
            sql = migration_file.read_text()
            try:
                await client.execute(sql)
                print("  ✓ Applied")
            except Exception as e:
                if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                    print("  ⊙ Already applied (skipped)")
                else:
                    print(f"  ✗ Failed: {e}")
                    raise
    finally:
        await client.close()


async def verify_connectivity() -> bool:
    """Verify database connectivity."""
    client = DatabaseClient()
    try:
        await client.start()
        result = await client.fetch_one("SELECT 1")
        return result is not None
    except Exception as e:
        print(f"Connectivity check failed: {e}")
        return False
    finally:
        await client.close()


async def main() -> int:
    print("=" * 50)
    print("Draftly Bootstrap")
    print("=" * 50)

    if not os.getenv("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set")
        return 1

    print("\n1. Running migrations...")
    await run_migrations()

    print("\n2. Verifying connectivity...")
    if await verify_connectivity():
        print("  ✓ Database connection successful")
    else:
        print("  ✗ Database connection failed")
        return 1

    print("\n" + "=" * 50)
    print("Bootstrap completed successfully")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
