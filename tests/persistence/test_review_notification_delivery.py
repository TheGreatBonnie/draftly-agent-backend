"""§9.4 verification: per-recipient review notification receipt persistence."""

from __future__ import annotations

from typing import Any

from draftly.persistence.repositories.review_notifications import (
    ReviewNotificationRepository,
)


class FakeClient:
    """Mirrors the real client's execute/fetch surface (asyncpg style)."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.fetched: list[tuple[str, tuple]] = []
        self._fetch_results: list[dict[str, Any] | None] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "OK"

    async def fetch_one(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.fetched.append((query, args))
        if self._fetch_results:
            return self._fetch_results.pop(0)
        return None

    def push_fetch(self, row: dict[str, Any]) -> None:
        self._fetch_results.append(row)


async def test_claim_inserts_claimed_row() -> None:
    client = FakeClient()
    client.push_fetch({"id": "receipt-1"})  # INSERT ... RETURNING id succeeds
    repo = ReviewNotificationRepository(database=client)

    claimed = await repo.claim("review-1", "org-1", "slack", "U1")

    assert claimed is True
    sql, params = client.fetched[0]
    assert "INSERT INTO review_notification_deliveries" in sql
    assert "ON CONFLICT" in sql and "DO NOTHING" in sql
    assert params[:4] == ("review-1", "org-1", "slack", "U1")


async def test_claim_reclaims_prior_failed_row() -> None:
    client = FakeClient()
    client.push_fetch(None)  # insert conflicts
    client.push_fetch({"id": "receipt-1"})  # reclaim UPDATE matched the failed row
    repo = ReviewNotificationRepository(database=client)

    claimed = await repo.claim("review-1", "org-1", "discord", "D1")

    assert claimed is True
    update_sql, params = client.fetched[1]
    assert "SET status = 'claimed'" in update_sql
    assert params[:3] == ("review-1", "discord", "D1")


async def test_claim_refuses_sent_delivery() -> None:
    client = FakeClient()
    client.push_fetch(None)  # insert conflicts
    client.push_fetch(None)  # reclaim UPDATE matched no 'failed' row
    repo = ReviewNotificationRepository(database=client)

    claimed = await repo.claim("review-1", "org-1", "slack", "U1")

    assert claimed is False
    assert client.executed == []


async def test_mark_sent_updates_receipt() -> None:
    client = FakeClient()
    repo = ReviewNotificationRepository(database=client)

    await repo.mark_sent("review-1", "org-1", "slack", "U1")

    sql, params = client.executed[0]
    assert "SET status = 'sent'" in sql
    assert "sent_at = now()" in sql
    assert params[:4] == ("review-1", "org-1", "slack", "U1")


async def test_mark_failed_updates_receipt_with_error() -> None:
    client = FakeClient()
    repo = ReviewNotificationRepository(database=client)

    await repo.mark_failed("review-1", "org-1", "slack", "U1", error="provider down")

    sql, params = client.executed[0]
    assert "SET status = 'failed'" in sql
    assert params[4] == "provider down"


async def test_migration_file_exists() -> None:
    from pathlib import Path

    migrations_dir = (
        Path(__file__).parent.parent.parent
        / "src"
        / "draftly"
        / "persistence"
        / "migrations"
    )
    migration = migrations_dir / "046_review_notification_deliveries.sql"
    assert migration.exists(), "046 migration is required for delivery receipts"
    text = migration.read_text()
    assert "review_notification_deliveries" in text
    assert "UNIQUE (review_id, platform, recipient_id)" in text
