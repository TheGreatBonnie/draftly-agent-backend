"""Repository configuration repository."""

from __future__ import annotations

import json
from typing import Any


class RepositoryConfigRepository:
    """CRUD for repositories table."""

    def __init__(self, client: Any = None) -> None:
        if client is None:
            from draftly.integrations.database.client import DatabaseClient
            self.client = DatabaseClient()
        else:
            self.client = client

    async def get(self, org_id: str, full_name: str) -> dict[str, Any] | None:
        return await self.client.fetch_one(
            "SELECT * FROM repositories WHERE org_id = $1 AND full_name = $2",
            org_id,
            full_name,
        )

    async def list_by_org(self, org_id: str) -> list[dict[str, Any]]:
        return await self.client.fetch_all(
            "SELECT * FROM repositories WHERE org_id = $1 ORDER BY created_at",
            org_id,
        )

    async def upsert(
        self,
        org_id: str,
        full_name: str,
        *,
        default_branch: str = "main",
        doc_include: list[str] | None = None,
        doc_exclude: list[str] | None = None,
        installation_id: int | None = None,
    ) -> dict[str, Any]:
        include = doc_include or ["README.md", "docs/**", "*.md", "*.mdx"]
        exclude = doc_exclude or ["node_modules/**", "dist/**"]

        await self.client.execute(
            "INSERT INTO repositories "
            "(org_id, full_name, default_branch, doc_include, doc_exclude, installation_id) "
            "VALUES ($1, $2, $3, $4::JSONB, $5::JSONB, $6) "
            "ON CONFLICT (org_id, full_name) DO UPDATE SET "
            "default_branch = $3, doc_include = $4::JSONB, doc_exclude = $5::JSONB, "
            "installation_id = $6",
            org_id,
            full_name,
            default_branch,
            json.dumps(include),
            json.dumps(exclude),
            installation_id,
        )
        return await self.get(org_id, full_name) or {"org_id": org_id, "full_name": full_name}
