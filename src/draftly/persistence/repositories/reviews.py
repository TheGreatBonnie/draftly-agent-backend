from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from integrations.cockroachdb.client import CockroachDBClient


@dataclass
class ReviewRecord:
    id: str
    org_id: str
    thread_id: str
    workflow: str
    tool_name: str
    tool_args: dict[str, Any]
    action_description: str | None
    status: str
    reviewer_id: str | None = None
    decision: str | None = None
    decision_comment: str | None = None
    decided_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    notification_sent_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ReviewsRepository:
    """CockroachDB repository for review requests and decisions."""

    def __init__(self, database: CockroachDBClient | None = None):
        self.database = database or CockroachDBClient()

    async def create_review(
        self,
        *,
        org_id: str,
        thread_id: str,
        workflow: str,
        tool_name: str,
        tool_args: dict[str, Any],
        action_description: str | None = None,
        expires_in_hours: int = 24,
    ) -> ReviewRecord:
        review_id = str(uuid4())
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=expires_in_hours)

        query = """
        INSERT INTO reviews (
            id, org_id, thread_id, workflow, tool_name, tool_args,
            action_description, status, created_at, expires_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending', $8, $9)
        """

        await self.database.execute(
            query, review_id, org_id, thread_id, workflow, tool_name,
            json.dumps(tool_args), action_description, now, expires_at,
        )

        return ReviewRecord(
            id=review_id, org_id=org_id, thread_id=thread_id,
            workflow=workflow, tool_name=tool_name, tool_args=tool_args,
            action_description=action_description, status="pending",
            created_at=now, expires_at=expires_at,
        )

    async def get_review(self, review_id: str) -> ReviewRecord | None:
        query = "SELECT * FROM reviews WHERE id = $1"
        row = await self.database.fetch_one(query, review_id)
        if not row:
            return None
        return self._row_to_record(row)

    async def record_decision(
        self,
        *,
        review_id: str,
        reviewer_id: str,
        decision: str,
        comment: str | None = None,
    ) -> ReviewRecord:
        now = datetime.now(UTC)
        query = """
        UPDATE reviews
        SET status = $2, reviewer_id = $3, decision = $2,
            decision_comment = $4, decided_at = $5
        WHERE id = $1 AND status = 'pending'
        RETURNING *
        """
        row = await self.database.fetch_one(
            query, review_id, decision, reviewer_id, comment, now,
        )
        if not row:
            existing = await self.get_review(review_id)
            if existing:
                return existing
            raise ValueError(f"Review {review_id} not found")
        return self._row_to_record(row)

    async def expire_old_reviews(self) -> list[ReviewRecord]:
        now = datetime.now(UTC)
        query = """
        UPDATE reviews
        SET status = 'expired', decision = 'timeout', decided_at = $1
        WHERE status = 'pending' AND expires_at < $1
        RETURNING *
        """
        rows = await self.database.fetch_all(query, now)
        return [self._row_to_record(row) for row in rows]

    async def mark_notification_sent(self, review_id: str) -> None:
        now = datetime.now(UTC)
        query = "UPDATE reviews SET notification_sent_at = $2 WHERE id = $1"
        await self.database.execute(query, review_id, now)

    def _row_to_record(self, row: Any) -> ReviewRecord:
        tool_args = row.get("tool_args", "{}")
        if isinstance(tool_args, str):
            tool_args = json.loads(tool_args)
        return ReviewRecord(
            id=str(row["id"]), org_id=str(row["org_id"]),
            thread_id=str(row["thread_id"]), workflow=str(row["workflow"]),
            tool_name=str(row["tool_name"]), tool_args=tool_args,
            action_description=row.get("action_description"),
            status=str(row["status"]), reviewer_id=row.get("reviewer_id"),
            decision=row.get("decision"), decision_comment=row.get("decision_comment"),
            decided_at=row.get("decided_at"),
            created_at=row.get("created_at", datetime.now(UTC)),
            expires_at=row.get("expires_at"),
            notification_sent_at=row.get("notification_sent_at"),
            metadata=row.get("metadata", {}),
        )
