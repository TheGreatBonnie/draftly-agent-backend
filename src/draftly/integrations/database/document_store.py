from __future__ import annotations

import json
from datetime import datetime
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
    source_hash,
    status,
    metadata,
    stale,
    outdated,
    incomplete,
    broken_links,
    unsupported_claims,
    created_at,
    updated_at,
    last_committed_at,
    draft_revision_id
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
        status: str | None = None,
        commit_sha: str | None = None,
        source_hash: str | None = None,
        last_committed_at: datetime | None = None,
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

            fields.append(f"metadata = ${len(params) + 1}::jsonb")
            params.append(json.dumps(metadata or {}))

            if title is not None:
                fields.append(f"title = ${len(params) + 1}")
                params.append(title)

            if document_type is not None:
                fields.append(f"document_type = ${len(params) + 1}")
                params.append(document_type)

            # Persist sync columns when provided
            for column, value in (
                ("status", status),
                ("commit_sha", commit_sha),
                ("source_hash", source_hash),
            ):
                if value is not None:
                    fields.append(f"{column} = ${len(params) + 1}")
                    params.append(value)

            if last_committed_at is not None:
                fields.append(f"last_committed_at = ${len(params) + 1}::timestamptz")
                params.append(last_committed_at)

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
            # Build INSERT dynamically so optional sync columns are included
            columns = ["repository", "path", "content", "metadata"]
            values: list[Any] = [repository, path, content, json.dumps(metadata or {})]
            for column, value in (
                ("org_id", org_id),
                ("title", title),
                ("document_type", document_type or "general"),
                ("status", status or "draft"),
                ("commit_sha", commit_sha),
                ("source_hash", source_hash),
                ("last_committed_at", last_committed_at),
            ):
                if value is not None:
                    columns.append(column)
                    values.append(value)

            placeholder_list = [f"${i}" for i in range(1, len(values) + 1)]
            placeholder_list[columns.index("metadata")] += "::jsonb"
            placeholders = ", ".join(placeholder_list)

            # Build ON CONFLICT update set for the optional sync columns so
            # concurrent upserts don't race on the (org_id, path) unique key.
            on_conflict_fields: list[str] = []
            on_conflict_set: list[str] = []
            if org_id is not None:
                on_conflict_fields = ["org_id", "path"]
                n = len(values)
                on_conflict_set = [
                    f"content = ${n + 1}",
                    f"metadata = ${n + 2}::jsonb",
                ]
                conflict_params: list[Any] = [content, json.dumps(metadata or {})]
                if title is not None:
                    n = len(values) + len(conflict_params)
                    on_conflict_set.append(f"title = ${n + 1}")
                    conflict_params.append(title)
                if document_type is not None:
                    n = len(values) + len(conflict_params)
                    on_conflict_set.append(f"document_type = ${n + 1}")
                    conflict_params.append(document_type)
                for column, value in (
                    ("status", status),
                    ("commit_sha", commit_sha),
                    ("source_hash", source_hash),
                    ("last_committed_at", last_committed_at),
                ):
                    if value is not None:
                        n = len(values) + len(conflict_params)
                        on_conflict_set.append(f"{column} = ${n + 1}")
                        conflict_params.append(value)
                on_conflict_set.append("updated_at = CURRENT_TIMESTAMP")
            else:
                on_conflict_fields = ["repository", "path"]
                n = len(values)
                on_conflict_set = [
                    f"content = ${n + 1}",
                    f"metadata = ${n + 2}::jsonb",
                ]
                conflict_params = [content, json.dumps(metadata or {})]
                on_conflict_set.append("updated_at = CURRENT_TIMESTAMP")

            conflict_clause = ""
            if on_conflict_fields:
                conflict_cols = ", ".join(on_conflict_fields)
                conflict_clause = (
                    f" ON CONFLICT ({conflict_cols}) DO UPDATE SET "
                    + ", ".join(on_conflict_set)
                )
                values.extend(conflict_params)

            row = await self.client.fetch_one(
                f"""
                INSERT INTO documentation ({", ".join(columns)})
                VALUES ({placeholders})
                {conflict_clause}
                RETURNING {_DOCUMENT_COLUMNS}
                """,
                *values,
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
                $1, $2, $3, $4, $5, $6, 'draft', $7::jsonb
            )
            RETURNING {_DOCUMENT_COLUMNS}
            """,
            document_id,
            org_id,
            path,
            title,
            content,
            document_type,
            json.dumps(metadata or {}),
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

    async def get_for_org(
        self,
        *,
        document_id: str,
        org_id: str,
    ) -> dict[str, Any] | None:
        row = await self.client.fetch_one(
            f"""
            SELECT {_DOCUMENT_COLUMNS}
            FROM documentation
            WHERE id = $1 AND org_id = $2
            """,
            document_id,
            org_id,
        )
        return self._row_to_dict(row) if row else None

    async def list_projection_by_org(
        self,
        *,
        org_id: str,
        repository: str | None,
        status: str | None,
        query: str | None,
        limit: int,
        cursor: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["org_id = $1"]
        args: list[Any] = [org_id]
        if repository:
            args.append(repository)
            clauses.append(f"repository = ${len(args)}")
        if status:
            if status == "published":
                clauses.append("status IN ('indexed', 'published')")
            else:
                args.append(status)
                clauses.append(f"status = ${len(args)}")
        if query:
            args.append(f"%{query}%")
            clauses.append(f"(title ILIKE ${len(args)} OR path ILIKE ${len(args)})")
        args.append(max(1, min(limit, 1000)))
        row_limit = len(args)
        rows = await self.client.fetch_all(
            f"""
            SELECT id, org_id, repository, path, title, document_type, version,
                   commit_sha, source_hash, status, metadata, stale, outdated,
                   incomplete, broken_links, unsupported_claims, created_at,
                   updated_at, last_committed_at, draft_revision_id,
                   (draft_revision_id IS NOT NULL) AS has_draft
            FROM documentation
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC, id DESC
            LIMIT ${row_limit}
            """,
            *args,
        )
        return [self._projection_to_dict(row) for row in rows]

    async def update(
        self,
        *,
        document_id: str,
        content: str | None = None,
        title: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
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

        if metadata is not None:
            fields.append(f"metadata = ${len(params) + 1}::jsonb")
            params.append(json.dumps(metadata))

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

    async def get_by_org_and_path(
        self,
        *,
        org_id: str,
        path: str,
    ) -> dict[str, Any] | None:
        row = await self.client.fetch_one(
            f"""
            SELECT {_DOCUMENT_COLUMNS}
            FROM documentation
            WHERE org_id = $1
              AND path = $2
            LIMIT 1
            """,
            org_id,
            path,
        )
        return self._row_to_dict(row) if row else None

    async def get_by_org_repository_path(
        self,
        *,
        org_id: str,
        repository: str,
        path: str,
    ) -> dict[str, Any] | None:
        row = await self.client.fetch_one(
            f"""
            SELECT {_DOCUMENT_COLUMNS}
            FROM documentation
            WHERE org_id = $1
              AND repository = $2
              AND path = $3
            LIMIT 1
            """,
            org_id,
            repository,
            path,
        )
        return self._row_to_dict(row) if row else None

    async def list_by_org(
        self,
        *,
        org_id: str,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        rows = await self.client.fetch_all(
            f"""
            SELECT {_DOCUMENT_COLUMNS}
            FROM documentation
            WHERE org_id = $1
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            org_id,
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
            "source_hash": row["source_hash"],
            "status": row["status"],
            "metadata": row["metadata"],
            "stale": row["stale"],
            "outdated": row["outdated"],
            "incomplete": row["incomplete"],
            "broken_links": row["broken_links"],
            "unsupported_claims": row["unsupported_claims"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_committed_at": row["last_committed_at"],
            "draft_revision_id": row["draft_revision_id"],
        }

    @staticmethod
    def _projection_to_dict(row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            return dict(row)
        return {key: row[key] for key in (
            "id", "org_id", "repository", "path", "title", "document_type",
            "version", "commit_sha", "source_hash", "status", "metadata",
            "stale", "outdated", "incomplete", "broken_links", "unsupported_claims",
            "created_at", "updated_at", "last_committed_at", "draft_revision_id", "has_draft",
        )}
