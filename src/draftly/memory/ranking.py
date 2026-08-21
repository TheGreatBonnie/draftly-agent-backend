"""Memory ranking (plan §8.1) — recency × importance × source quality."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


class MemoryRanking:
    """Score and order memory records for retrieval."""

    def __init__(
        self,
        *,
        importance_weight: float = 0.4,
        recency_weight: float = 0.3,
        similarity_weight: float = 0.2,
        source_weight: float = 0.1,
        half_life_days: float = 30.0,
    ) -> None:
        self.importance_weight = importance_weight
        self.recency_weight = recency_weight
        self.similarity_weight = similarity_weight
        self.source_weight = source_weight
        self.half_life_days = half_life_days

    def recency_score(self, record: dict[str, Any]) -> float:
        """Exponential decay by age; 1.0 when unknown/brand new."""
        created = record.get("created_at")
        if not created:
            return 1.0
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                return 1.0
        now = datetime.now(UTC)
        age_days = max((now - created).total_seconds() / 86400.0, 0.0)
        return 0.5 ** (age_days / self.half_life_days)

    def source_score(self, record: dict[str, Any]) -> float:
        """Source quality from metadata; defaults to confidence."""
        metadata = record.get("metadata") or {}
        quality = metadata.get("source_quality")
        if isinstance(quality, (int, float)):
            return float(quality)
        return float(record.get("confidence", 0.5))

    def score(self, record: dict[str, Any], similarity: float = 0.0) -> float:
        """Composite relevance score in [0, 1]."""
        return (
            self.importance_weight * float(record.get("importance", 0.5))
            + self.recency_weight * self.recency_score(record)
            + self.similarity_weight * max(0.0, min(1.0, similarity))
            + self.source_weight * self.source_score(record)
        )

    def rank(
        self,
        records: list[dict[str, Any]],
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return records ordered by composite score, highest first."""
        scored = [
            (self.score(record, float(record.get("similarity", 0.0))), record)
            for record in records
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [record for _, record in scored[:limit]]
