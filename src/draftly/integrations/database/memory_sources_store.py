from __future__ import annotations

from typing import Any

from draftly.integrations.database.client import DatabaseClient


class MemorySourcesStore:
    def __init__(
        self,
        client: DatabaseClient | None = None,
    ) -> None:
        self.client = client or DatabaseClient()

    async def insert(
        self,
        *,
        org_id: str,
        memory_item_id: str,
        source_type: str,
        source_id: str | None = None,
        source_url: str | None = None,
        repository: str | None = None,
        commit_sha: str | None = None,
        content_hash: str | None = None,
        evidence: str | None = None,
    ) -> dict[str, Any]:
        row = await self.client.fetch_one(
            """
            INSERT INTO memory_sources (
                org_id,
                memory_item_id,
                source_type,
                source_id,
                source_url,
                repository,
                commit_sha,
                content_hash,
                evidence
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9
            )
            RETURNING id, org_id, memory_item_id, source_type, source_id
            """,
            org_id,
            memory_item_id,
            source_type,
            source_id,
            source_url,
            repository,
            commit_sha,
            content_hash,
            evidence,
        )

        return dict(row)
