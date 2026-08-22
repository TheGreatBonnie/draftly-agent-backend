from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from draftly.integrations.database.client import DatabaseClient
from draftly.support.events import SupportEvent
from draftly.support.models import (
    SupportMessage,
    SupportThread,
)


class SupportRepository:
    """
    NeonDB repository for support conversations,
    messages, threads, and support events.
    """

    def __init__(self, database: DatabaseClient | None = None):
        self.database = database or DatabaseClient()

    # ---------------------------------------------------------
    # Messages
    # ---------------------------------------------------------

    async def save_message(
        self,
        message: SupportMessage,
        *,
        org_id: str | None = None,
    ) -> SupportMessage:

        query = """
        INSERT INTO support_messages (
            message_id,
            platform,
            channel_id,
            channel_name,
            author_id,
            author_name,
            content,
            thread_id,
            timestamp,
            url,
            raw,
            org_id
        )
        VALUES (
            $1, $2, $3, $4, $5,
            $6, $7, $8, $9, $10, $11, $12
        )
        ON CONFLICT (message_id, platform)
        DO UPDATE SET
            content = EXCLUDED.content,
            raw = EXCLUDED.raw,
            org_id = EXCLUDED.org_id
        """

        effective_org_id = org_id if org_id is not None else message.org_id

        await self.database.execute(
            query,
            message.id,
            message.platform,
            message.channel_id,
            message.channel_name,
            message.author_id,
            message.author_name,
            message.content,
            message.thread_id,
            message.timestamp,
            message.url,
            message.raw or {},
            effective_org_id,
        )

        message.org_id = effective_org_id
        return message

    # ---------------------------------------------------------
    # Threads
    # ---------------------------------------------------------

    async def save_thread(
        self,
        thread: SupportThread,
    ) -> SupportThread:

        query = """
        INSERT INTO support_threads (
            thread_id,
            platform,
            channel_id,
            channel_name,
            root_message_id,
            created_at,
            updated_at,
            raw,
            org_id
        )
        VALUES (
            $1, $2, $3, $4,
            $5, $6, $7, $8, $9
        )
        ON CONFLICT (thread_id, platform)
        DO UPDATE SET
            updated_at = EXCLUDED.updated_at,
            raw = EXCLUDED.raw,
            org_id = EXCLUDED.org_id
        """

        await self.database.execute(
            query,
            thread.id,
            thread.platform,
            thread.channel_id,
            thread.channel_name,
            thread.root_message_id,
            thread.created_at,
            thread.updated_at,
            thread.raw or {},
            thread.org_id,
        )

        for message in thread.messages:
            await self.save_message(
                message,
                org_id=thread.org_id,
            )

        return thread

    async def get_thread(
        self,
        thread_id: str,
    ) -> SupportThread | None:

        query = """
        SELECT
            thread_id,
            platform,
            channel_id,
            channel_name,
            root_message_id,
            created_at,
            updated_at,
            raw,
            org_id
        FROM support_threads
        WHERE thread_id = $1
        """

        row = await self.database.fetch_one(
            query,
            thread_id,
        )

        if row is None:
            return None

        message_query = """
        SELECT
            message_id,
            platform,
            channel_id,
            channel_name,
            author_id,
            author_name,
            content,
                thread_id,
                timestamp,
                url,
                raw,
                org_id
        FROM support_messages
        WHERE thread_id = $1
        ORDER BY timestamp ASC
        """

        message_rows = await self.database.fetch_all(
            message_query,
            thread_id,
        )

        messages = [
            SupportMessage(
                id=str(item["message_id"]),
                platform=item["platform"],
                channel_id=item["channel_id"],
                channel_name=item["channel_name"],
                author_id=item["author_id"],
                author_name=item["author_name"],
                content=item["content"],
                thread_id=item["thread_id"],
                timestamp=item["timestamp"],
                url=item["url"],
                raw=item["raw"],
                org_id=item["org_id"],
            )
            for item in message_rows
        ]

        return SupportThread(
            id=str(row["thread_id"]),
            platform=row["platform"],
            channel_id=row["channel_id"],
            channel_name=row["channel_name"],
            root_message_id=str(row["root_message_id"]),
            messages=messages,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            raw=row["raw"],
            org_id=row["org_id"],
        )

    # ---------------------------------------------------------
    # Search
    # ---------------------------------------------------------

    async def search_messages(
        self,
        query: str,
        *,
        platform: str | None = None,
        limit: int = 20,
    ) -> Sequence[SupportMessage]:

        if platform:
            sql = """
            SELECT
                message_id,
                platform,
                channel_id,
                channel_name,
                author_id,
                author_name,
                content,
                thread_id,
                timestamp,
                url,
                raw,
                org_id
            FROM support_messages
            WHERE platform = $1
              AND content ILIKE $2
            ORDER BY timestamp DESC
            LIMIT $3
            """

            rows = await self.database.fetch_all(
                sql,
                platform,
                f"%{query}%",
                limit,
            )

        else:
            sql = """
            SELECT
                message_id,
                platform,
                channel_id,
                channel_name,
                author_id,
                author_name,
                content,
                thread_id,
                timestamp,
                url,
                raw,
                org_id
            FROM support_messages
            WHERE content ILIKE $1
            ORDER BY timestamp DESC
            LIMIT $2
            """

            rows = await self.database.fetch_all(
                sql,
                f"%{query}%",
                limit,
            )

        return [
            SupportMessage(
                id=str(row["message_id"]),
                platform=row["platform"],
                channel_id=row["channel_id"],
                channel_name=row["channel_name"],
                author_id=row["author_id"],
                author_name=row["author_name"],
                content=row["content"],
                thread_id=row["thread_id"],
                timestamp=row["timestamp"],
                url=row["url"],
                raw=row["raw"],
                org_id=row["org_id"],
            )
            for row in rows
        ]

    # ---------------------------------------------------------
    # Events
    # ---------------------------------------------------------

    async def save_event(
        self,
        event: SupportEvent,
    ) -> SupportEvent:

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
            $1,
            $2,
            $3,
            $4,
            $5,
            $6,
            $7
        )
        ON CONFLICT (event_id)
        DO NOTHING
        """

        await self.database.execute(
            query,
            event.event_id,
            event.platform,
            event.event_type,
            None,
            event.occurred_at,
            event.actor_id,
            event.payload,
        )

        return event

    async def list_events(
        self,
        *,
        platform: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> Sequence[SupportEvent]:

        conditions = []
        args = []

        if platform:
            args.append(platform)
            conditions.append(f"source = ${len(args)}")

        if since:
            args.append(since)
            conditions.append(f"occurred_at >= ${len(args)}")

        where = ""

        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        args.append(limit)

        query = f"""
        SELECT
            event_id,
            source,
            event_type,
            occurred_at,
            actor,
            payload
        FROM events
        {where}
        ORDER BY occurred_at DESC
        LIMIT ${len(args)}
        """

        rows = await self.database.fetch_all(
            query,
            *args,
        )

        return [
            SupportEvent(
                event_id=str(row["event_id"]),
                platform=row["source"],
                event_type=row["event_type"],
                occurred_at=row["occurred_at"],
                actor_id=row["actor"],
                payload=row["payload"],
            )
            for row in rows
        ]
