"""Support resolver (plan §8.7) — resolve questions, update memory."""

from __future__ import annotations

from typing import Any

import structlog

from draftly.feedback.knowledge_updater import KnowledgeUpdater
from draftly.support.models import SupportAnswer, SupportQuestion

logger = structlog.get_logger(__name__)


class SupportResolver:
    """Close the loop on answered questions."""

    def __init__(self, knowledge_updater: KnowledgeUpdater | None = None) -> None:
        self.knowledge = knowledge_updater or KnowledgeUpdater()

    async def resolve(
        self,
        *,
        question: SupportQuestion,
        answer: SupportAnswer,
    ) -> dict[str, Any]:
        """Record a resolved Q/A pair into memory."""
        record = await self.knowledge.record_solution(
            question=question.content,
            answer=answer.content,
            platform=question.platform,
            org_id=question.org_id,
            question_id=question.question_id,
        )
        logger.info(
            "support_resolved question_id=%s confidence=%s",
            question.question_id,
            answer.confidence,
        )
        return {"resolved": True, "memory": record}
