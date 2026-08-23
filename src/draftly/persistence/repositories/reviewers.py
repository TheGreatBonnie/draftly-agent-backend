from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from draftly.integrations.database.client import DatabaseClient

logger = structlog.get_logger(__name__)


@dataclass
class ReviewerRecord:
    id: str
    org_id: str
    name: str
    email: str | None
    slack_user_id: str | None
    discord_user_id: str | None
    clerk_user_id: str | None
    notify_slack: bool
    notify_discord: bool
    notify_email: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ReviewersRepository:
    """NeonDB repository for reviewer CRUD operations."""

    def __init__(self, database: DatabaseClient | None = None):
        self.database = database or DatabaseClient()

    # ── Read ──────────────────────────────────────────────────

    async def get_active_reviewers(self, org_id: str) -> list[ReviewerRecord]:
        query = """
        SELECT * FROM reviewers
        WHERE org_id = $1 AND is_active = true
        ORDER BY created_at ASC
        """
        rows = await self.database.fetch_all(query, org_id)
        return [self._row_to_record(row) for row in rows]

    async def get_reviewers_by_org(
        self,
        org_id: str,
        active_only: bool = True,
    ) -> list[ReviewerRecord]:
        query = "SELECT * FROM reviewers WHERE org_id = $1"
        if active_only:
            query += " AND is_active = true"
        query += " ORDER BY created_at DESC"
        rows = await self.database.fetch_all(query, org_id)
        return [self._row_to_record(row) for row in rows]

    async def get_reviewer_by_id(self, reviewer_id: str) -> ReviewerRecord | None:
        row = await self.database.fetch_one(
            "SELECT * FROM reviewers WHERE id = $1",
            reviewer_id,
        )
        return self._row_to_record(row) if row else None

    async def get_reviewer_by_clerk_user(
        self,
        org_id: str,
        clerk_user_id: str,
    ) -> ReviewerRecord | None:
        row = await self.database.fetch_one(
            "SELECT * FROM reviewers WHERE org_id = $1 AND clerk_user_id = $2",
            org_id,
            clerk_user_id,
        )
        return self._row_to_record(row) if row else None

    async def get_active_reviewer_ids(self, org_id: str) -> list[str]:
        rows = await self.database.fetch_all(
            "SELECT id::text FROM reviewers WHERE org_id = $1 AND is_active = true",
            org_id,
        )
        return [str(row["id"]) for row in rows]

    # ── Create ────────────────────────────────────────────────

    async def create_reviewer(
        self,
        org_id: str,
        name: str,
        email: str | None = None,
        slack_user_id: str | None = None,
        discord_user_id: str | None = None,
        notify_slack: bool = True,
        notify_discord: bool = False,
        notify_email: bool = False,
        clerk_user_id: str | None = None,
    ) -> ReviewerRecord:
        row = await self.database.fetch_one(
            """
            INSERT INTO reviewers (org_id, name, email, slack_user_id,
                                   discord_user_id, notify_slack, notify_discord,
                                   notify_email, clerk_user_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING *
            """,
            org_id,
            name,
            email,
            slack_user_id,
            discord_user_id,
            notify_slack,
            notify_discord,
            notify_email,
            clerk_user_id,
        )
        if row is None:
            raise RuntimeError("reviewer row missing after insert")
        logger.info("reviewer_created id=%s org_id=%s name=%s", row["id"], org_id, name)
        return self._row_to_record(row)

    # ── Update ────────────────────────────────────────────────

    async def update_reviewer(
        self,
        reviewer_id: str,
        **kwargs: Any,
    ) -> ReviewerRecord:
        allowed_fields = {
            "name",
            "email",
            "slack_user_id",
            "discord_user_id",
            "notify_slack",
            "notify_discord",
            "notify_email",
            "is_active",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            raise ValueError("No valid fields to update")

        set_clauses: list[str] = []
        values: list[Any] = []
        for i, (key, value) in enumerate(updates.items(), 1):
            set_clauses.append(f"{key} = ${i}")
            values.append(value)

        values.append(reviewer_id)
        set_clause = ", ".join(set_clauses)

        row = await self.database.fetch_one(
            f"""
            UPDATE reviewers
            SET {set_clause}, updated_at = now()
            WHERE id = ${len(values)}
            RETURNING *
            """,
            *values,
        )
        if row is None:
            raise RuntimeError("reviewer row missing after update")
        logger.info("reviewer_updated id=%s fields=%s", reviewer_id, list(updates.keys()))
        return self._row_to_record(row)

    # ── Delete ────────────────────────────────────────────────

    async def delete_reviewer(self, reviewer_id: str) -> None:
        await self.database.execute(
            "DELETE FROM reviewers WHERE id = $1",
            reviewer_id,
        )
        logger.info("reviewer_deleted id=%s", reviewer_id)

    # ── Serialization ─────────────────────────────────────────

    @staticmethod
    def _row_to_record(row: Any) -> ReviewerRecord:
        return ReviewerRecord(
            id=str(row["id"]),
            org_id=str(row["org_id"]),
            name=str(row["name"]),
            email=row.get("email"),
            slack_user_id=row.get("slack_user_id"),
            discord_user_id=row.get("discord_user_id"),
            clerk_user_id=row.get("clerk_user_id"),
            notify_slack=bool(row.get("notify_slack", False)),
            notify_discord=bool(row.get("notify_discord", False)),
            notify_email=bool(row.get("notify_email", False)),
            is_active=bool(row.get("is_active", True)),
            created_at=row.get("created_at", datetime.now(UTC)),
            updated_at=row.get("updated_at", datetime.now(UTC)),
        )

    @staticmethod
    def record_to_dict(record: ReviewerRecord) -> dict[str, Any]:
        """Serialize a ReviewerRecord to a JSON-safe dict."""
        return {
            "id": record.id,
            "org_id": record.org_id,
            "name": record.name,
            "email": record.email,
            "slack_user_id": record.slack_user_id,
            "discord_user_id": record.discord_user_id,
            "clerk_user_id": record.clerk_user_id,
            "notify_slack": record.notify_slack,
            "notify_discord": record.notify_discord,
            "notify_email": record.notify_email,
            "is_active": record.is_active,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }
