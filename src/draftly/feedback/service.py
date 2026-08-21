"""Feedback service (plan §8.5) — feedback loop orchestration."""

from __future__ import annotations

import logging

from draftly.feedback.classifier import FeedbackClassifier
from draftly.feedback.deduplication import DeduplicationService
from draftly.feedback.gap_detector import GapDetector
from draftly.feedback.models import (
    DocumentationGapCandidate,
    FeedbackItem,
)
from draftly.feedback.prioritization import GapPrioritizer
from draftly.persistence.repositories.support import SupportRepository

logger = logging.getLogger(__name__)


class FeedbackService:
    """Support messages → deduplicated, classified, prioritized gaps."""

    def __init__(
        self,
        support_repository: SupportRepository | None = None,
        classifier: FeedbackClassifier | None = None,
        deduplicator: DeduplicationService | None = None,
        gap_detector: GapDetector | None = None,
        prioritizer: GapPrioritizer | None = None,
    ) -> None:
        self.support = support_repository or SupportRepository()
        self.classifier = classifier or FeedbackClassifier()
        self.deduplicator = deduplicator or DeduplicationService()
        self.gap_detector = gap_detector or GapDetector()
        self.prioritizer = prioritizer or GapPrioritizer()

    async def collect_questions(
        self,
        *,
        platform: str | None = None,
        limit: int = 200,
    ) -> list[FeedbackItem]:
        """Pull recent support messages as feedback items."""
        rows = await self.support.search_messages(
            query="%",
            platform=platform,
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
                timestamp=row.timestamp,
                org_id=getattr(row, "org_id", None),
            )
            for row in rows
        ]
        return [self.classifier.classify(item) for item in items]

    async def detect_gaps(
        self,
        *,
        platform: str | None = None,
        min_occurrences: int | None = None,
        limit: int | None = 10,
    ) -> list[DocumentationGapCandidate]:
        """End-to-end: collect → dedupe → cluster → prioritize."""
        items = await self.collect_questions(platform=platform)
        unique_items = self.deduplicator.deduplicate(items)
        candidates = self.gap_detector.detect_gaps(
            unique_items,
            min_occurrences=min_occurrences,
        )
        return self.prioritizer.prioritize(candidates, limit=limit)
