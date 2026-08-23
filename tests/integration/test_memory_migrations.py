"""Migration integration tests — require NEON_DATABASE_URL."""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("NEON_DATABASE_URL"),
    reason="NEON_DATABASE_URL not set",
)

MIGRATIONS = Path("src/draftly/persistence/migrations")


@pytest.mark.asyncio
async def test_new_memory_migrations_apply_idempotently():
    import asyncpg

    conn = await asyncpg.connect(os.environ["NEON_DATABASE_URL"])
    try:
        for name in (
            "028_episodes.sql",
            "029_procedures.sql",
            "030_doc_relations.sql",
            "031_memory_candidates.sql",
        ):
            sql = (MIGRATIONS / name).read_text()
            await conn.execute(sql)
            await conn.execute(sql)  # second run must not raise either
        for table in (
            "episodes",
            "procedures",
            "knowledge_nodes",
            "doc_edges",
            "memory_candidates",
        ):
            await conn.fetchrow(f"SELECT 1 FROM {table} LIMIT 1")
    finally:
        await conn.close()
