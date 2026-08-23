"""Scheduled memory-maintenance workflow wrapper."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


async def run_memory_maintenance(context: Any) -> dict[str, int]:
    """Weekly forgetting pass. Each policy fails open, independently."""
    from draftly.memory.maintenance import MemoryMaintenance

    maintenance = MemoryMaintenance()
    summaries = 0
    demoted = 0
    try:
        summaries = await maintenance.archive_old_episodes()
    except Exception:
        logger.exception("episode_archival_failed")
    try:
        demoted = await maintenance.demote_stale_semantic_memory()
    except Exception:
        logger.exception("stale_demotion_failed")
    return {"episode_summaries": summaries, "demoted": demoted}
