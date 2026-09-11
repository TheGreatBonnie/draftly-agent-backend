"""Organization-scoped read repository for curated Knowledge."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from draftly.integrations.database.client import DatabaseClient
from draftly.integrations.database.memory_feedback_store import MemoryFeedbackStore
from draftly.integrations.database.memory_links_store import MemoryLinksStore
from draftly.integrations.database.memory_sources_store import MemorySourcesStore
from draftly.integrations.database.vector_search import VectorSearch
from draftly.memory.embeddings import EmbeddingService

_LIST_COLUMNS = """
    id, org_id, namespace, memory_type, content, summary, status,
    importance, confidence, version, access_count, last_accessed_at,
    created_at, updated_at
"""


class KnowledgeRepository:
    """Own the database boundary used by the Knowledge API."""

    def __init__(self, *, client: DatabaseClient, embedder: EmbeddingService) -> None:
        self.client = client
        self.embedder = embedder
        self.vector_search = VectorSearch(client=client)
        self.sources = MemorySourcesStore(client=client)
        self.links = MemoryLinksStore(client=client)
        self.feedback = MemoryFeedbackStore(client=client)

    @staticmethod
    def _require_org_id(org_id: str) -> str:
        value = org_id.strip()
        if not value:
            raise ValueError("org_id is required for Knowledge reads")
        return value

    @staticmethod
    def _encode_cursor(updated_at: Any, item_id: str) -> str:
        payload = {"updated_at": updated_at.isoformat(), "id": item_id}
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[str, str]:
        padding = "=" * (-len(cursor) % 4)
        try:
            payload = json.loads(
                base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
            )
            updated_at = str(payload["updated_at"])
            item_id = str(payload["id"])
            datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            UUID(item_id)
        except (
            KeyError,
            TypeError,
            ValueError,
            binascii.Error,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError("Invalid Knowledge cursor") from exc
        return updated_at, item_id

    @staticmethod
    def _status_clause(status: str | None) -> tuple[str, list[Any]]:
        if status is None:
            return "", []
        clauses = {
            "verified": "status = 'active' AND confidence >= 0.5",
            "needs-verification": "status = 'active' AND confidence < 0.5",
            "stale": "status <> 'active'",
        }
        try:
            return f" AND {clauses[status]}", []
        except KeyError as exc:
            raise ValueError(f"Unsupported Knowledge status: {status}") from exc

    async def list_page(
        self,
        *,
        org_id: str,
        status: str | None = None,
        limit: int = 25,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        organization = self._require_org_id(org_id)
        if not 1 <= limit <= 100:
            raise ValueError("Knowledge page limit must be between 1 and 100")
        status_clause, _ = self._status_clause(status)
        params: list[Any] = [organization]
        cursor_clause = ""
        if cursor:
            updated_at, item_id = self._decode_cursor(cursor)
            params.extend([updated_at, item_id])
            cursor_clause = " AND (updated_at, id) < ($2::timestamptz, $3::uuid)"
        params.append(limit + 1)
        rows = await self.client.fetch_all(
            f"""
            SELECT {_LIST_COLUMNS}
            FROM memory_items
            WHERE org_id = $1
              AND namespace = 'knowledge'
              {status_clause}
              {cursor_clause}
            ORDER BY updated_at DESC, id DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
        count = await self.client.fetch_one(
            f"""
            SELECT count(*)::int AS total
            FROM memory_items
            WHERE org_id = $1
              AND namespace = 'knowledge'
              {status_clause}
            """,
            organization,
        )
        has_next = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor = None
        if has_next and page_rows:
            last = page_rows[-1]
            next_cursor = self._encode_cursor(last["updated_at"], str(last["id"]))
        return {
            "items": [self._row_to_item(row) for row in page_rows],
            "total": int((count or {}).get("total", 0)),
            "next_cursor": next_cursor,
        }

    async def stats(self, *, org_id: str) -> dict[str, int]:
        organization = self._require_org_id(org_id)
        row = await self.client.fetch_one(
            """
            SELECT
                count(*)::int AS total,
                count(*) FILTER (
                    WHERE status = 'active' AND confidence >= 0.5
                )::int AS verified,
                count(*) FILTER (
                    WHERE status = 'active' AND confidence < 0.5
                )::int AS needs_verification,
                count(*) FILTER (
                    WHERE status <> 'active'
                )::int AS stale
            FROM memory_items
            WHERE org_id = $1 AND namespace = 'knowledge'
            """,
            organization,
        )
        values = row or {}
        return {
            "total": int(values.get("total", 0)),
            "verified": int(values.get("verified", 0)),
            "needs_verification": int(values.get("needs_verification", 0)),
            "stale": int(values.get("stale", 0)),
        }

    async def search(
        self,
        *,
        org_id: str,
        query: str,
        limit: int = 20,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        organization = self._require_org_id(org_id)
        if not 1 <= limit <= 50:
            raise ValueError("Knowledge search limit must be between 1 and 50")
        embedding = await self.embedder.embed(query)
        rows = await self.vector_search.search(
            namespace="knowledge",
            embedding=embedding,
            limit=limit,
            org_id=organization,
        )
        items = [
            {**self._row_to_item(row), "similarity": row.get("similarity")}
            for row in rows
        ]
        if status is not None:
            self._status_clause(status)
            items = [item for item in items if item["status"] == status]
        return items

    async def detail(self, *, org_id: str, item_id: str) -> dict[str, Any] | None:
        organization = self._require_org_id(org_id)
        row = await self.client.fetch_one(
            f"""
            SELECT {_LIST_COLUMNS}
            FROM memory_items
            WHERE id = $1::uuid AND org_id = $2 AND namespace = 'knowledge'
            """,
            item_id,
            organization,
        )
        if row is None:
            return None
        sources, related, feedback = await self._related(
            organization,
            item_id,
        )
        item = self._row_to_item(row)
        item.pop("namespace", None)
        item.pop("memory_type", None)
        return {
            **item,
            "description": row.get("content"),
            "sources": sources,
            "related": related,
            "feedback": feedback,
        }

    async def source_summaries(self, *, org_id: str) -> list[dict[str, Any]]:
        organization = self._require_org_id(org_id)
        rows = await self.client.fetch_all(
            """
            SELECT ms.source_type,
                   ms.repository,
                   count(DISTINCT ms.memory_item_id)::int AS item_count,
                   count(*)::int AS evidence_count,
                   max(ms.created_at) AS last_seen_at
            FROM memory_sources AS ms
            JOIN memory_items AS mi ON mi.id = ms.memory_item_id
            WHERE ms.org_id = $1
              AND mi.org_id = $1
              AND mi.namespace = 'knowledge'
            GROUP BY ms.source_type, ms.repository
            ORDER BY item_count DESC, ms.source_type ASC, ms.repository ASC
            """,
            organization,
        )
        return [dict(row) for row in rows]

    async def graph(
        self,
        *,
        org_id: str,
        limit_nodes: int = 100,
        limit_edges: int = 200,
    ) -> dict[str, list[dict[str, Any]]]:
        organization = self._require_org_id(org_id)
        if not 1 <= limit_nodes <= 200 or not 1 <= limit_edges <= 500:
            raise ValueError("Knowledge graph limits are out of range")
        rows = await self.client.fetch_all(
            f"""
            SELECT {_LIST_COLUMNS}
            FROM memory_items
            WHERE org_id = $1 AND namespace = 'knowledge'
            ORDER BY updated_at DESC, importance DESC, id DESC
            LIMIT $2
            """,
            organization,
            limit_nodes,
        )
        nodes = {str(row["id"]): row for row in rows}
        if not nodes:
            return {"nodes": [], "edges": []}
        links = await self.client.fetch_all(
            """
            SELECT source_memory_id, target_memory_id, relationship, confidence
            FROM memory_links
            WHERE org_id = $1
              AND source_memory_id = ANY($2::uuid[])
              AND target_memory_id = ANY($2::uuid[])
            ORDER BY created_at DESC
            LIMIT $3
            """,
            organization,
            list(nodes),
            limit_edges,
        )
        return {
            "nodes": [self._graph_node(row) for row in nodes.values()],
            "edges": [
                {
                    "source": str(row["source_memory_id"]),
                    "target": str(row["target_memory_id"]),
                    "relationship": row["relationship"],
                    "confidence": row.get("confidence"),
                }
                for row in links
            ],
        }

    async def topics(self, *, org_id: str, limit: int = 20) -> list[dict[str, Any]]:
        organization = self._require_org_id(org_id)
        if not 1 <= limit <= 100:
            raise ValueError("Knowledge topic limit must be between 1 and 100")
        rows = await self.client.fetch_all(
            """
            SELECT topic,
                   count(*)::int AS item_count,
                   count(*) FILTER (
                       WHERE mi.status = 'active' AND mi.confidence >= 0.5
                   )::int AS verified_count,
                   count(*) FILTER (
                       WHERE mi.status <> 'active'
                   )::int AS stale_count
            FROM memory_items AS mi
            CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE
                    WHEN jsonb_typeof(mi.metadata->'topics') = 'array'
                    THEN mi.metadata->'topics'
                    ELSE '[]'::jsonb
                END
            ) AS topic
            WHERE mi.org_id = $1 AND mi.namespace = 'knowledge'
            GROUP BY topic
            ORDER BY item_count DESC, topic ASC
            LIMIT $2
            """,
            organization,
            limit,
        )
        return [
            {
                "name": str(row["topic"]).strip(),
                "item_count": int(row["item_count"]),
                "verified_count": int(row["verified_count"]),
                "stale_count": int(row["stale_count"]),
            }
            for row in rows
            if str(row.get("topic", "")).strip()
        ]

    async def embedding_stats(self, *, org_id: str) -> dict[str, Any]:
        organization = self._require_org_id(org_id)
        counts = await self.client.fetch_one(
            """
            SELECT count(DISTINCT mi.id)::int AS total_items,
                   count(DISTINCT me.memory_item_id)::int AS embedded_items,
                   max(me.created_at) AS last_embedded_at
            FROM memory_items AS mi
            LEFT JOIN memory_embeddings AS me
              ON me.memory_item_id = mi.id AND me.org_id = $1
            WHERE mi.org_id = $1 AND mi.namespace = 'knowledge'
            """,
            organization,
        )
        models = await self.client.fetch_all(
            """
            SELECT DISTINCT me.model
            FROM memory_embeddings AS me
            JOIN memory_items AS mi ON mi.id = me.memory_item_id
            WHERE me.org_id = $1 AND mi.org_id = $1 AND mi.namespace = 'knowledge'
            ORDER BY me.model
            """,
            organization,
        )
        values = counts or {}
        total = int(values.get("total_items", 0))
        embedded = int(values.get("embedded_items", 0))
        return {
            "total_items": total,
            "embedded_items": embedded,
            "coverage_percent": round((embedded / total) * 100, 2) if total else 0.0,
            "models": [str(row["model"]) for row in models],
            "last_embedded_at": values.get("last_embedded_at"),
        }

    async def _related(
        self, org_id: str, item_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        return (
            [
                self._source_projection(row)
                for row in await self.sources.list_by_memory(
                    org_id=org_id, memory_item_id=item_id
                )
            ],
            [
                self._link_projection(row)
                for row in await self.links.list_by_memory(
                    org_id=org_id, memory_item_id=item_id
                )
            ],
            [
                self._feedback_projection(row)
                for row in await self.feedback.list_by_memory(
                    org_id=org_id, memory_item_id=item_id
                )
            ],
        )

    @staticmethod
    def _source_projection(row: Any) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "source_type": row["source_type"],
            "source_id": row.get("source_id"),
            "source_url": row.get("source_url"),
            "repository": row.get("repository"),
            "commit_sha": row.get("commit_sha"),
            "evidence": row.get("evidence"),
        }

    @staticmethod
    def _link_projection(row: Any) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "relationship": row["relationship"],
            "source_memory_id": str(row["source_memory_id"]),
            "target_memory_id": str(row["target_memory_id"]),
            "confidence": row.get("confidence"),
        }

    @staticmethod
    def _feedback_projection(row: Any) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "feedback_type": row["feedback_type"],
            "source": row.get("source"),
            "score": row.get("score"),
            "comment": row.get("comment"),
            "created_at": row.get("created_at"),
        }

    @staticmethod
    def _row_to_item(row: Any) -> dict[str, Any]:
        status = row.get("status")
        confidence = float(row.get("confidence") or 0.0)
        ui_status = (
            "stale"
            if status not in ("active", None)
            else "verified"
            if confidence >= 0.5
            else "needs-verification"
        )
        return {
            "id": str(row["id"]),
            "entity": row.get("summary") or row.get("content"),
            "description": row.get("summary"),
            "status": ui_status,
            "importance": row.get("importance"),
            "confidence": row.get("confidence"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "namespace": row.get("namespace", "knowledge"),
            "memory_type": row.get("memory_type", "knowledge"),
        }

    @classmethod
    def _graph_node(cls, row: Any) -> dict[str, Any]:
        item = cls._row_to_item(row)
        return {
            "id": item["id"],
            "label": item["entity"] or item["id"],
            "status": item["status"],
            "memory_type": item["memory_type"],
        }
