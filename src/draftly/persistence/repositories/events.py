from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from draftly.events.github.events import GitHubEvent
from draftly.integrations.database.client import DatabaseClient


class EventRepository:
    """
    Persistence repository for Draftly events.

    This repository owns database persistence.
    It does not know how GitHub's API works.
    """

    def __init__(self, database: DatabaseClient | None = None):
        self.database = database or DatabaseClient()

    async def save_event(
        self,
        event: GitHubEvent,
    ) -> GitHubEvent:
        query = """
        INSERT INTO events (
            event_id,
            source,
            event_type,
            repository,
            occurred_at,
            actor,
            payload
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7
        )
        ON CONFLICT (event_id)
        DO NOTHING
        """

        await self.database.execute(
            query,
            event.event_id,
            "github",
            event.event_type,
            event.repository,
            event.occurred_at,
            event.actor,
            event.payload,
        )

        return event

    async def get_event(
        self,
        event_id: str,
    ) -> GitHubEvent | None:
        query = """
        SELECT
            event_id,
            event_type,
            repository,
            occurred_at,
            actor,
            payload
        FROM events
        WHERE event_id = $1
          AND source = 'github'
        """

        row = await self.database.fetch_one(
            query,
            event_id,
        )

        if row is None:
            return None

        return GitHubEvent(
            event_id=row["event_id"],
            event_type=row["event_type"],
            repository=row["repository"],
            occurred_at=row["occurred_at"],
            actor=row["actor"],
            payload=row["payload"],
        )

    async def get_event_by_id(self, event_id: str) -> dict[str, Any] | None:
        """Source-agnostic event lookup — Slack/Discord and GitHub both claim rows."""
        query = """
        SELECT event_id, source, event_type, repository, actor, org_id, payload, occurred_at
        FROM events
        WHERE event_id = $1
        """
        row = await self.database.fetch_one(query, event_id)
        if row is None:
            return None
        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        return {
            "event_id": str(row["event_id"]),
            "source": str(row["source"]),
            "event_type": str(row["event_type"]),
            "repository": row.get("repository"),
            "actor": row.get("actor"),
            "org_id": str(row["org_id"]) if row.get("org_id") is not None else None,
            "payload": payload,
            "occurred_at": row.get("occurred_at"),
        }

    async def list_events(
        self,
        repository: str,
        event_type: str | None = None,
        limit: int = 100,
    ) -> Sequence[GitHubEvent]:
        if event_type:
            query = """
            SELECT
                event_id,
                event_type,
                repository,
                occurred_at,
                actor,
                payload
            FROM events
            WHERE source = 'github'
              AND repository = $1
              AND event_type = $2
            ORDER BY occurred_at DESC
            LIMIT $3
            """

            rows = await self.database.fetch_all(
                query,
                repository,
                event_type,
                limit,
            )
        else:
            query = """
            SELECT
                event_id,
                event_type,
                repository,
                occurred_at,
                actor,
                payload
            FROM events
            WHERE source = 'github'
              AND repository = $1
            ORDER BY occurred_at DESC
            LIMIT $2
            """

            rows = await self.database.fetch_all(
                query,
                repository,
                limit,
            )

        return [
            GitHubEvent(
                event_id=row["event_id"],
                event_type=row["event_type"],
                repository=row["repository"],
                occurred_at=row["occurred_at"],
                actor=row["actor"],
                payload=row["payload"],
            )
            for row in rows
        ]

    async def find_recent_events(
        self,
        repository: str,
        since: datetime,
        limit: int = 100,
    ) -> Sequence[GitHubEvent]:
        query = """
        SELECT
            event_id,
            event_type,
            repository,
            occurred_at,
            actor,
            payload
        FROM events
        WHERE source = 'github'
          AND repository = $1
          AND occurred_at >= $2
        ORDER BY occurred_at DESC
        LIMIT $3
        """

        rows = await self.database.fetch_all(
            query,
            repository,
            since,
            limit,
        )

        return [
            GitHubEvent(
                event_id=row["event_id"],
                event_type=row["event_type"],
                repository=row["repository"],
                occurred_at=row["occurred_at"],
                actor=row["actor"],
                payload=row["payload"],
            )
            for row in rows
        ]

    # ========================================================
    # Runner support (plan §7.4): idempotency + status marking
    # ========================================================

    async def try_claim(
        self,
        event_id: str,
        *,
        source: str = "github",
        event_type: str = "unknown",
        repository: str | None = None,
        actor: str | None = None,
        payload: dict[str, Any] | None = None,
        org_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> bool:
        """Atomically claim an event for processing.

        INSERT .. ON CONFLICT DO NOTHING RETURNING makes the
        duplicate-replay check and the insert a single atomic step:
        True ⇒ this caller owns the run; False ⇒ someone else already
        recorded it.
        """
        query = """
        INSERT INTO events (
            event_id, org_id, event_type, source, repository,
            actor, payload, occurred_at, status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7::JSONB, $8, 'running')
        ON CONFLICT (event_id) DO NOTHING
        RETURNING event_id
        """

        row = await self.database.fetch_one(
            query,
            event_id,
            org_id,
            event_type,
            source,
            repository,
            actor,
            json.dumps(payload or {}),
            occurred_at or datetime.now(UTC),
        )

        return row is not None

    async def find_by_event_id(self, event_id: str) -> dict[str, Any] | None:
        """Look up any recorded event by its unique event_id."""
        query = """
        SELECT event_id, event_type, source, status, processed_at
        FROM events
        WHERE event_id = $1
        """

        row = await self.database.fetch_one(query, event_id)
        if row is None:
            return None

        return {
            "event_id": str(row["event_id"]),
            "event_type": str(row["event_type"]),
            "source": str(row["source"]),
            "status": str(row["status"]),
            "processed_at": row.get("processed_at"),
        }

    async def mark_status(
        self,
        event_id: str,
        status: str,
    ) -> None:
        """Record a terminal/in-flight run status on the event row."""
        query = """
        UPDATE events
        SET status = $2, processed_at = $3
        WHERE event_id = $1
        """

        await self.database.execute(
            query,
            event_id,
            status,
            datetime.now(UTC),
        )

    async def list_recent_runs(self, *, limit: int = 100) -> Sequence[dict[str, Any]]:
        """Recent claimed events with their status, for reconciliation sweeps."""
        query = """
        SELECT event_id, status
        FROM events
        ORDER BY created_at DESC
        LIMIT $1
        """

        rows = await self.database.fetch_all(query, limit)
        return [
            {"event_id": str(r["event_id"]), "status": str(r["status"])}
            for r in rows
        ]
