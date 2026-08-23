"""Episodic memory facade — record what happened, recall similar past runs."""

from __future__ import annotations

from typing import Any

import structlog

from draftly.integrations.database.episodes_store import EpisodesStore
from draftly.memory.embeddings import EmbeddingService

logger = structlog.get_logger(__name__)


class EpisodicService:
    """Record what happened per run; recall similar past episodes."""

    def __init__(self, store: Any = None, embeddings: Any = None) -> None:
        self.store = store or EpisodesStore()
        self.embeddings = embeddings or EmbeddingService()

    async def record_episode(self, **fields: Any) -> dict[str, Any]:
        summary = fields.get("summary") or fields.get("trigger_summary") or ""
        fields["embedding"] = self.embeddings.embed(summary)
        record = await self.store.insert(fields=fields)
        logger.debug("episode_recorded id=%s", record.get("id"))
        return record

    async def find_similar(
        self,
        query: str,
        *,
        org_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        try:
            return await self.store.search(
                embedding=self.embeddings.embed(query),
                org_id=org_id,
                limit=limit,
            )
        except Exception:
            logger.exception("episode_recall_failed")
            return []
