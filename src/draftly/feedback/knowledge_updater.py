"""Knowledge updater (plan §8.5) — learn from resolved issues."""

from __future__ import annotations

import logging
from typing import Any

from draftly.memory.models import Knowledge, Solution
from draftly.memory.repository import MemoryNamespaces
from draftly.memory.service import MemoryService

logger = logging.getLogger(__name__)


class KnowledgeUpdater:
    """Persist validated answers/resolutions into memory."""

    def __init__(self, memory: MemoryService | None = None) -> None:
        self.memory = memory or MemoryService()

    async def record_solution(
        self,
        *,
        question: str,
        answer: str,
        platform: str = "slack",
        org_id: str | None = None,
        question_id: str | None = None,
    ) -> dict[str, Any]:
        """Store a Q/A pair as a solution memory."""
        solution = Solution(
            namespace=MemoryNamespaces.SOLUTIONS,
            content=answer,
            importance=0.7,
            confidence=0.8,
            question_id=question_id,
            resolution_status="resolved",
            metadata={"question": question, "platform": platform},
            org_id=org_id,
        )
        return await self.memory.remember(solution)

    async def record_knowledge(
        self,
        *,
        topic: str,
        content: str,
        source_quality: float = 0.8,
        org_id: str | None = None,
    ) -> dict[str, Any]:
        knowledge = Knowledge(
            namespace=MemoryNamespaces.KNOWLEDGE,
            content=content,
            importance=0.8,
            confidence=source_quality,
            topic=topic,
            source_quality=source_quality,
            org_id=org_id,
        )
        return await self.memory.remember(knowledge)

    async def update_from_resolution(
        self,
        *,
        question: str,
        answer: str,
        platform: str = "slack",
        org_id: str | None = None,
    ) -> dict[str, Any]:
        """Full loop: store the solution and consolidate near-duplicates."""
        record = await self.record_solution(
            question=question,
            answer=answer,
            platform=platform,
            org_id=org_id,
        )
        await self.memory.consolidate(
            namespace=MemoryNamespaces.SOLUTIONS,
            query=answer,
        )
        return record
