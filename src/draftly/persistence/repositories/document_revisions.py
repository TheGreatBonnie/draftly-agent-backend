from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from draftly.integrations.database.client import DatabaseClient


class RevisionConflict(Exception):  # noqa: N818 - public API name from the plan
    """The document changed since the editor loaded its base revision."""

    def __init__(
        self,
        message: str = "Document has changed since it was loaded",
        *,
        current_source_hash: str | None = None,
        current_revision_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.current_source_hash = current_source_hash
        self.current_revision_id = current_revision_id


@dataclass(frozen=True)
class DocumentationRevision:
    id: str
    document_id: str
    org_id: str
    revision_number: int
    origin: str
    status: str
    title: str | None
    content: str
    base_source_hash: str | None
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class RevisionPage:
    items: list[DocumentationRevision]
    total: int
    next_cursor: str | None


class DocumentRevisionRepository:
    def __init__(self, database: DatabaseClient | None = None) -> None:
        self.database = database or DatabaseClient()

    async def create_draft(
        self,
        *,
        document_id: str,
        org_id: str,
        content: str,
        title: str | None,
        base_source_hash: str | None,
        base_revision_id: str | None,
        created_by: str,
    ) -> DocumentationRevision:
        async with self.database.transaction(isolation="serializable") as conn:
            document = await conn.fetchrow(
                """
                SELECT id, org_id, source_hash, updated_at, draft_revision_id
                FROM documentation
                WHERE id = $1 AND org_id = $2
                FOR UPDATE
                """,
                document_id,
                org_id,
            )
            if document is None:
                raise RevisionConflict(
                    f"Document '{document_id}' not found",
                    current_source_hash=None,
                    current_revision_id=None,
                )
            self._validate_base(
                document,
                base_source_hash=base_source_hash,
                base_revision_id=base_revision_id,
            )
            return await self._insert_revision(
                conn,
                document=document,
                document_id=document_id,
                org_id=org_id,
                content=content,
                title=title,
                created_by=created_by,
                origin="manual",
            )

    async def list_revisions(
        self,
        *,
        document_id: str,
        org_id: str,
        limit: int,
        cursor: str | None = None,
    ) -> RevisionPage:
        safe_limit = max(1, min(limit, 100))
        cursor_number = int(cursor) if cursor and cursor.isdigit() else None
        rows = await self.database.fetch_all(
            """
            SELECT id, document_id, org_id, revision_number, origin, status,
                   title, content, base_source_hash, created_by, created_at
            FROM documentation_revisions
            WHERE document_id = $1 AND org_id = $2
              AND ($4::INT8 IS NULL OR revision_number < $4)
            ORDER BY revision_number DESC
            LIMIT $3
            """,
            document_id,
            org_id,
            safe_limit + 1,
            cursor_number,
        )
        items = [self._to_revision(row) for row in rows[:safe_limit]]
        next_cursor = str(items[-1].revision_number) if len(rows) > safe_limit else None
        count = await self.database.fetch_one(
            """
            SELECT COUNT(*) AS total
            FROM documentation_revisions
            WHERE document_id = $1 AND org_id = $2
            """,
            document_id,
            org_id,
        )
        return RevisionPage(
            items=items,
            total=int(count["total"] if count else len(items)),
            next_cursor=next_cursor,
        )

    async def get_revision(
        self,
        *,
        document_id: str,
        revision_id: str,
        org_id: str,
    ) -> DocumentationRevision | None:
        row = await self.database.fetch_one(
            """
            SELECT id, document_id, org_id, revision_number, origin, status,
                   title, content, base_source_hash, created_by, created_at
            FROM documentation_revisions
            WHERE id = $1 AND document_id = $2 AND org_id = $3
            """,
            revision_id,
            document_id,
            org_id,
        )
        return self._to_revision(row) if row else None

    async def restore_revision(
        self,
        *,
        document_id: str,
        revision_id: str,
        org_id: str,
        created_by: str,
    ) -> DocumentationRevision:
        async with self.database.transaction(isolation="serializable") as conn:
            document = await conn.fetchrow(
                """
                SELECT id, org_id, source_hash, updated_at, draft_revision_id
                FROM documentation
                WHERE id = $1 AND org_id = $2
                FOR UPDATE
                """,
                document_id,
                org_id,
            )
            if document is None:
                raise RevisionConflict(f"Document '{document_id}' not found")
            original = await conn.fetchrow(
                """
                SELECT id, document_id, org_id, revision_number, origin, status,
                       title, content, base_source_hash, created_by, created_at
                FROM documentation_revisions
                WHERE id = $1 AND document_id = $2 AND org_id = $3
                """,
                revision_id,
                document_id,
                org_id,
            )
            if original is None:
                raise RevisionConflict(f"Revision '{revision_id}' not found")
            return await self._insert_revision(
                conn,
                document=document,
                document_id=document_id,
                org_id=org_id,
                content=original["content"],
                title=original["title"],
                created_by=created_by,
                origin="restore",
            )

    async def _insert_revision(
        self,
        conn: Any,
        *,
        document: Any,
        document_id: str,
        org_id: str,
        content: str,
        title: str | None,
        created_by: str,
        origin: str,
    ) -> DocumentationRevision:
        max_row = await conn.fetchrow(
            """
            SELECT COALESCE(MAX(revision_number), 0) AS revision_number
            FROM documentation_revisions
            WHERE document_id = $1 AND org_id = $2
            """,
            document_id,
            org_id,
        )
        revision_number = int(max_row["revision_number"] if max_row else 0) + 1
        current_revision_id = (
            document.get("draft_revision_id")
            if isinstance(document, dict)
            else document["draft_revision_id"]
        )
        if current_revision_id:
            await conn.execute(
                """
                UPDATE documentation_revisions
                SET status = 'superseded'
                WHERE id = $1 AND document_id = $2 AND org_id = $3
                """,
                current_revision_id,
                document_id,
                org_id,
            )
        revision_id = str(uuid4())
        row = await conn.fetchrow(
            """
            INSERT INTO documentation_revisions (
                id, document_id, org_id, revision_number, origin, title,
                content, base_source_hash, base_document_updated_at, created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING id, document_id, org_id, revision_number, origin, status,
                      title, content, base_source_hash, created_by, created_at
            """,
            revision_id,
            document_id,
            org_id,
            revision_number,
            origin,
            title,
            content,
            document["source_hash"],
            document["updated_at"],
            created_by,
        )
        await conn.execute(
            """
            UPDATE documentation
            SET draft_revision_id = $1
            WHERE id = $2 AND org_id = $3
            """,
            revision_id,
            document_id,
            org_id,
        )
        return self._to_revision(row)

    @staticmethod
    def _validate_base(
        document: Any,
        *,
        base_source_hash: str | None,
        base_revision_id: str | None,
    ) -> None:
        current_source_hash = document["source_hash"]
        current_revision_id = document["draft_revision_id"]
        if base_source_hash is not None and base_source_hash != current_source_hash:
            raise RevisionConflict(
                current_source_hash=current_source_hash,
                current_revision_id=current_revision_id,
            )
        if base_revision_id is not None and base_revision_id != current_revision_id:
            raise RevisionConflict(
                current_source_hash=current_source_hash,
                current_revision_id=current_revision_id,
            )

    @staticmethod
    def _to_revision(row: Any) -> DocumentationRevision:
        return DocumentationRevision(
            id=str(row["id"]),
            document_id=str(row["document_id"]),
            org_id=str(row["org_id"]),
            revision_number=int(row["revision_number"]),
            origin=row["origin"],
            status=row["status"],
            title=row["title"],
            content=row["content"],
            base_source_hash=row["base_source_hash"],
            created_by=row["created_by"],
            created_at=row["created_at"],
        )
