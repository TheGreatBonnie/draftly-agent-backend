"""Feedback service (plan §8.5) — feedback loop orchestration."""

from __future__ import annotations

from typing import Any

import structlog

from draftly.feedback.classifier import FeedbackClassifier
from draftly.feedback.deduplication import DeduplicationService
from draftly.feedback.gap_detector import GapDetector
from draftly.feedback.models import (
    DocumentationGapCandidate,
    FeedbackItem,
)
from draftly.feedback.prioritization import GapPrioritizer
from draftly.persistence.repositories.support import SupportRepository

logger = structlog.get_logger(__name__)


class FeedbackService:
    """Support messages → deduplicated, classified, prioritized gaps."""

    def __init__(
        self,
        support_repository: SupportRepository | None = None,
        feedback_repository: Any | None = None,
        classifier: FeedbackClassifier | None = None,
        deduplicator: DeduplicationService | None = None,
        gap_detector: GapDetector | None = None,
        prioritizer: GapPrioritizer | None = None,
    ) -> None:
        self.support = support_repository or SupportRepository()
        self.feedback = feedback_repository
        self.classifier = classifier or FeedbackClassifier()
        self.deduplicator = deduplicator or DeduplicationService()
        self.gap_detector = gap_detector or GapDetector()
        self.prioritizer = prioritizer or GapPrioritizer()

    async def collect_questions(
        self,
        *,
        org_id: str,
        platform: str | None = None,
        limit: int = 200,
    ) -> list[FeedbackItem]:
        """Pull recent support messages as feedback items."""
        if not org_id:
            raise ValueError("org_id is required for feedback collection")
        rows = await self.support.search_messages(
            query="%",
            platform=platform,
            org_id=org_id,
            limit=limit,
        )
        items = [
            FeedbackItem(
                id=str(row.id),
                platform=row.platform,
                content=row.content,
                author=row.author_name,
                channel=row.channel_name,
                source_message_id=f"{row.channel_id}:{row.thread_id or row.id}",
                source_event_id=f"{row.channel_id}:{row.thread_id or row.id}",
                source_url=getattr(row, "url", None),
                timestamp=row.timestamp,
                org_id=getattr(row, "org_id", None),
            )
            for row in rows
        ]
        items = [self.classifier.classify(item) for item in items]
        if self.feedback is not None:
            items.extend(await self.feedback.list_recent(org_id, platform=platform, limit=limit))
        return [self.classifier.classify(item) for item in items]

    async def detect_gaps(
        self,
        *,
        org_id: str,
        platform: str | None = None,
        min_occurrences: int | None = None,
        limit: int | None = 10,
    ) -> list[DocumentationGapCandidate]:
        """End-to-end: collect → dedupe → cluster → prioritize."""
        items = await self.collect_questions(org_id=org_id, platform=platform)
        unique_items = self.deduplicator.deduplicate(items)
        candidates = self.gap_detector.detect_gaps(
            unique_items,
            min_occurrences=min_occurrences,
        )
        return self.prioritizer.prioritize(candidates, limit=limit)
