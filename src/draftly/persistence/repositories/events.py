from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from domain.github.events import GitHubEvent
from integrations.cockroachdb.client import CockroachDBClient


class EventRepository:
    """
    Persistence repository for Draftly events.

    This repository owns database persistence.
    It does not know how GitHub's API works.
    """

    def __init__(self, database: CockroachDBClient | None = None):
        self.database = database or CockroachDBClient()

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
