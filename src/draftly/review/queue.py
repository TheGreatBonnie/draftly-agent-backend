"""Review queue (plan §8.3) — pending reviews and expiry."""

from __future__ import annotations

from typing import Any

from draftly.persistence.repositories.reviews import ReviewsRepository


class ReviewQueue:
    """Query and expire the pending review queue."""

    def __init__(self, repository: ReviewsRepository | None = None) -> None:
        self.repository = repository or ReviewsRepository()

    async def pending(self, org_id: str | None = None) -> list[Any]:
        """List pending reviews, oldest first."""
        records = await self.repository.list_reviews(status="pending")
        if org_id:
            records = [r for r in records if r.org_id == org_id]
        return sorted(records, key=lambda r: r.created_at)

    async def expire_stale(self) -> list[Any]:
        """Expire reviews past their deadline; returns expired records."""
        return await self.repository.expire_old_reviews()

    async def get(self, review_id: str) -> Any | None:
        return await self.repository.get_review(review_id)
