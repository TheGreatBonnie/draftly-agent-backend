from __future__ import annotations

from typing import Any
from uuid import uuid4

from draftly.integrations.database.client import DatabaseClient

_DOCUMENT_COLUMNS = """
    id,
    org_id,
    repository,
    path,
    title,
    content,
    document_type,
    version,
    commit_sha,
    status,
    metadata,
    stale,
    outdated,
    incomplete,
    broken_links,
    unsupported_claims,
    created_at,
    updated_at
"""


class DocumentStore:
    """
    Async documentation persistence on the `documentation` table.

    Exposes both the repository/path API and the org-based API
    previously spread across DocumentStore and NeonDocumentsStore.
    """

    def __init__(
        self,
        client: DatabaseClient | None = None,
    ) -> None:
        self.client = client or DatabaseClient()

    # ------------------------------------------------------------------
    # Repository/path API
    # ------------------------------------------------------------------

    async def search_documents(
        self,
        *,
        repository: str,
        query: str | None = None,
        paths: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        sql = f"""
            SELECT {_DOCUMENT_COLUMNS}
            FROM documentation
            WHERE repository = $1
        """
        args: list[Any] = [repository]

        if paths:
            sql += " AND path = ANY($2)"
            args.append(paths)

        if query:
            sql += f" AND content ILIKE ${len(args) + 1}"
            args.append(f"%{query}%")

        sql += " ORDER BY updated_at DESC"

        rows = await self.client.fetch_all(sql, *args)

        return [self._row_to_dict(row) for row in rows]

    async def get_document(
        self,
        *,
        repository: str,
        path: str,
    ) -> dict[str, Any] | None:
        row = await self.client.fetch_one(
            f"""
            SELECT {_DOCUMENT_COLUMNS}
            FROM documentation
            WHERE repository = $1
              AND path = $2
            LIMIT 1
            """,
            repository,
            path,
        )

        return self._row_to_dict(row) if row else None

    async def list_documents(
        self,
        *,
        repository: str,
    ) -> list[dict[str, Any]]:
        return await self.search_documents(repository=repository)

    async def upsert_document(
        self,
        *,
        org_id: str | None = None,
        repository: str,
        path: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        title: str | None = None,
        document_type: str | None = None,
    ) -> dict[str, Any]:
        existing = await self.client.fetch_one(
            """
            SELECT id
            FROM documentation
            WHERE repository = $1 AND path = $2
            LIMIT 1
            """,
            repository,
            path,
        )

        if existing is not None:
            fields = ["content = $1"]
            params: list[Any] = [content]

            fields.append(f"metadata = ${len(params) + 1}")
            params.append(metadata or {})

            if title is not None:
                fields.append(f"title = ${len(params) + 1}")
                params.append(title)

            if document_type is not None:
                fields.append(f"document_type = ${len(params) + 1}")
                params.append(document_type)

            fields.append("updated_at = CURRENT_TIMESTAMP")
            params.append(existing["id"])

            row = await self.client.fetch_one(
                f"""
                UPDATE documentation
                SET {", ".join(fields)}
                WHERE id = ${len(params)}
                RETURNING {_DOCUMENT_COLUMNS}
                """,
                *params,
            )
        else:
            row = await self.client.fetch_one(
                f"""
                INSERT INTO documentation (
                    org_id,
                    repository,
                    path,
                    title,
                    document_type,
                    content,
                    metadata
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING {_DOCUMENT_COLUMNS}
                """,
                org_id,
                repository,
                path,
                title,
                document_type or "general",
                content,
                metadata or {},
            )

        return self._row_to_dict(row)

    # ------------------------------------------------------------------
    # Org-based API
    # ------------------------------------------------------------------

    async def insert(
        self,
        *,
        org_id: str,
        path: str,
        title: str,
        content: str,
        document_type: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        document_id = str(uuid4())

        row = await self.client.fetch_one(
            f"""
            INSERT INTO documentation (
                id,
                org_id,
                path,
                title,
                content,
                document_type,
                status,
                metadata
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, 'draft', $7
            )
            RETURNING {_DOCUMENT_COLUMNS}
            """,
            document_id,
            org_id,
            path,
            title,
            content,
            document_type,
            metadata,
        )

        return self._row_to_dict(row)

    async def get(
        self,
        *,
        document_id: str,
    ) -> dict[str, Any] | None:
        row = await self.client.fetch_one(
            f"""
            SELECT {_DOCUMENT_COLUMNS}
            FROM documentation
            WHERE id = $1
            """,
            document_id,
        )

        return self._row_to_dict(row) if row else None

    async def update(
        self,
        *,
        document_id: str,
        content: str | None = None,
        title: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        fields = []
        params: list[Any] = []

        if content is not None:
            fields.append(f"content = ${len(params) + 1}")
            params.append(content)

        if title is not None:
            fields.append(f"title = ${len(params) + 1}")
            params.append(title)

        if status is not None:
            fields.append(f"status = ${len(params) + 1}")
            params.append(status)

        fields.append("updated_at = CURRENT_TIMESTAMP")

        params.append(document_id)

        row = await self.client.fetch_one(
            f"""
            UPDATE documentation
            SET {", ".join(fields)}
            WHERE id = ${len(params)}
            RETURNING {_DOCUMENT_COLUMNS}
            """,
            *params,
        )

        if row is None:
            raise ValueError(f"Document '{document_id}' not found.")

        return self._row_to_dict(row)

    async def search(
        self,
        *,
        org_id: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        pattern = f"%{query}%"

        rows = await self.client.fetch_all(
            f"""
            SELECT {_DOCUMENT_COLUMNS}
            FROM documentation
            WHERE org_id = $1
              AND (
                  title ILIKE $2
                  OR content ILIKE $2
              )
            LIMIT $3
            """,
            org_id,
            pattern,
            limit,
        )

        return [self._row_to_dict(row) for row in rows]

    async def delete(
        self,
        *,
        document_id: str,
    ) -> bool:
        await self.client.execute(
            """
            DELETE FROM documentation
            WHERE id = $1
            """,
            document_id,
        )

        return True

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            return dict(row)

        if row is None:
            return {}

        return {
            "id": str(row["id"]),
            "org_id": (str(row["org_id"]) if row["org_id"] else None),
            "repository": row["repository"],
            "path": row["path"],
            "title": row["title"],
            "content": row["content"],
            "document_type": row["document_type"],
            "version": row["version"],
            "commit_sha": row["commit_sha"],
            "status": row["status"],
            "metadata": row["metadata"],
            "stale": row["stale"],
            "outdated": row["outdated"],
            "incomplete": row["incomplete"],
            "broken_links": row["broken_links"],
            "unsupported_claims": row["unsupported_claims"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
