"""Memory maintenance — forgetting policies (spec 2026-08-23 §Components 7).

Soft eviction only: archival compression of old episodes and demotion of
stale low-importance semantic memory. Nothing here hard-deletes.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

EPISODE_RETENTION_DAYS = 180
STALE_IMPORTANCE_FLOOR = 0.3
STALE_ACCESS_DAYS = 90


class MemoryMaintenance:
    def __init__(self, client: Any = None) -> None:
        from draftly.integrations.database.client import DatabaseClient

        self.client = client or DatabaseClient()

    async def archive_old_episodes(self) -> int:
        """Collapse episodes older than the retention window into one
        archived summary memory per org/trigger_type/month."""
        row = await self.client.fetch_one(
            """
            INSERT INTO memory_items (
                org_id, namespace, memory_type, content,
                status, importance, confidence
            )
            SELECT org_id, 'knowledge', 'episode_summary',
                   'Archived activity summary: ' || trigger_type || ' — '
                       || count(*) || ' episodes between '
                       || min(date_trunc('month', created_at))::date
                       || ' and '
                       || max(date_trunc('month', created_at))::date,
                   'archived', 0.4, 0.6
            FROM episodes
            WHERE created_at < now() - ($1::TEXT || ' days')::INTERVAL
            GROUP BY org_id, trigger_type, date_trunc('month', created_at)
            RETURNING 1
            """,
            str(EPISODE_RETENTION_DAYS),
        )
        archived = 1 if row else 0
        logger.info("episodes_archived_summaries=%d", archived)
        return archived

    async def demote_stale_semantic_memory(self) -> int:
        """Archive active semantic memory that is stale and rarely accessed.

        Decisions are never auto-demoted (they document why the software is
        the way it is); superseded records are left untouched.
        """
        result = await self.client.execute(
            """
            UPDATE memory_items SET status = 'archived', updated_at = now()
            WHERE status = 'active'
              AND importance < $1
              AND (last_accessed_at IS NULL OR last_accessed_at < now()
                   - ($2::TEXT || ' days')::INTERVAL)
              AND memory_type <> 'decision'
            """,
            STALE_IMPORTANCE_FLOOR,
            str(STALE_ACCESS_DAYS),
        )
        count = int(result.split()[-1]) if result and result.split() else 0
        logger.info("stale_memory_demoted=%d", count)
        return count
