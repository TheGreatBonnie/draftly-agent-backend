from __future__ import annotations

from datetime import datetime
from typing import Any

from draftly.integrations.database.document_store import DocumentStore


class DocumentRepository:
    def __init__(
        self,
        store: DocumentStore | None = None,
    ) -> None:
        self.store = store or DocumentStore()

    async def create(
        self,
        *,
        org_id: str,
        path: str,
        title: str,
        content: str,
        document_type: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.store.insert(
            org_id=org_id,
            path=path,
            title=title,
            content=content,
            document_type=document_type,
            metadata=metadata,
        )

    async def find_by_repository(
        self,
        *,
        repository: str,
    ) -> list[dict[str, Any]]:
        return await self.store.list_documents(repository=repository)

    async def save(
        self,
        *,
        repository: str,
        path: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.store.upsert_document(
            repository=repository,
            path=path,
            content=content,
            metadata=metadata or {},
        )

    async def upsert(
        self,
        *,
        org_id: str | None = None,
        repository: str,
        path: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        title: str | None = None,
        document_type: str | None = None,
        status: str | None = None,
        commit_sha: str | None = None,
        source_hash: str | None = None,
        last_committed_at: datetime | None = None,
    ) -> dict[str, Any]:
        return await self.store.upsert_document(
            org_id=org_id,
            repository=repository,
            path=path,
            content=content,
            metadata=metadata,
            title=title,
            document_type=document_type,
            status=status,
            commit_sha=commit_sha,
            source_hash=source_hash,
            last_committed_at=last_committed_at,
        )

    async def get_by_org_and_path(
        self, *, org_id: str, path: str
    ) -> dict[str, Any] | None:
        return await self.store.get_by_org_and_path(org_id=org_id, path=path)

    async def list_by_org(
        self, *, org_id: str, limit: int = 1000
    ) -> list[dict[str, Any]]:
        return await self.store.list_by_org(org_id=org_id, limit=limit)

    async def get(
        self,
        document_id: str,
    ) -> dict[str, Any] | None:
        return await self.store.get(document_id=document_id)

    async def get_by_repository_path(
        self,
        *,
        repository: str,
        path: str,
    ) -> dict[str, Any] | None:
        return await self.store.get_document(
            repository=repository,
            path=path,
        )

    async def update(
        self,
        document_id: str,
        *,
        content: str | None = None,
        title: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.store.update(
            document_id=document_id,
            content=content,
            title=title,
            status=status,
            metadata=metadata,
        )

    async def search(
        self,
        *,
        org_id: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await self.store.search(
            org_id=org_id,
            query=query,
            limit=limit,
        )

    async def delete(
        self,
        document_id: str,
    ) -> bool:
        return await self.store.delete(document_id=document_id)
