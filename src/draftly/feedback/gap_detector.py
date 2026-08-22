"""Gap detector (plan §8.5) — documentation gaps from support signals."""

from __future__ import annotations

import re
from collections import defaultdict

from draftly.feedback.models import (
    DocumentationGapCandidate,
    FeedbackCluster,
    FeedbackItem,
)

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "in",
    "for",
    "on",
    "is",
    "are",
    "with",
    "how",
    "do",
    "does",
    "i",
    "my",
    "we",
    "our",
    "it",
    "when",
    "what",
    "why",
    "can",
    "there",
    "way",
    "get",
    "use",
    "using",
}


class GapDetector:
    """Cluster feedback items into topics and promote hot clusters."""

    def __init__(self, min_cluster_size: int = 2) -> None:
        self.min_cluster_size = min_cluster_size

    @staticmethod
    def _keywords(text: str, limit: int = 4) -> list[str]:
        words = [w for w in re.findall(r"[a-z][a-z0-9_-]{2,}", text.lower()) if w not in STOPWORDS]
        return words[:limit]

    def topic_for(self, item: FeedbackItem) -> str:
        keywords = self._keywords(item.content)
        return "-".join(keywords) if keywords else "misc"

    def cluster(self, items: list[FeedbackItem]) -> list[FeedbackCluster]:
        """Group items by shared leading keywords."""
        groups: dict[str, list[FeedbackItem]] = defaultdict(list)
        for item in items:
            groups[self.topic_for(item)].append(item)
        return [
            FeedbackCluster(
                topic=topic,
                items=group,
                platforms=sorted({i.platform for i in group}),
            )
            for topic, group in sorted(groups.items())
        ]

    def detect_gaps(
        self,
        items: list[FeedbackItem],
        *,
        min_occurrences: int | None = None,
    ) -> list[DocumentationGapCandidate]:
        """Promote clusters at/above the threshold to gap candidates."""
        threshold = min_occurrences or self.min_cluster_size
        candidates = []
        for cluster in self.cluster(items):
            if cluster.size < threshold:
                continue
            negative = sum(1 for i in cluster.items if i.sentiment == "negative")
            severity = min(1.0, 0.25 * cluster.size + 0.1 * negative)
            candidates.append(
                DocumentationGapCandidate(
                    topic=cluster.topic,
                    occurrences=cluster.size,
                    severity=round(severity, 2),
                    platforms=cluster.platforms,
                    sample_questions=[i.content for i in cluster.items[:5]],
                )
            )
        candidates.sort(key=lambda c: (-c.severity, -c.occurrences))
        return candidates
