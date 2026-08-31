"""Async store for the documentation knowledge graph."""

from __future__ import annotations

import json
from typing import Any

from draftly.integrations.database.client import DatabaseClient


class DocRelationsStore:
    def __init__(self, client: DatabaseClient | None = None) -> None:
        self.client = client or DatabaseClient()

    async def ensure_node(
        self,
        *,
        node_type: str,
        key: str,
        org_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        row = await self.client.fetch_one(
            """
            INSERT INTO knowledge_nodes (org_id, node_type, key, title)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (org_id, node_type, key)
            DO UPDATE SET title = COALESCE(EXCLUDED.title, knowledge_nodes.title)
            RETURNING id, org_id, node_type, key, title
            """,
            org_id,
            node_type,
            key,
            title,
        )
        return dict(row) if row else {}

    async def upsert_edge(
        self,
        *,
        source_node_id: str,
        target_node_id: str,
        relation_type: str,
        org_id: str | None = None,
        evidence: list | None = None,
    ) -> dict[str, Any]:
        row = await self.client.fetch_one(
            """
            INSERT INTO doc_edges (
                org_id, source_node_id, target_node_id, relation_type, evidence
            ) VALUES ($1, $2::UUID, $3::UUID, $4, $5::JSONB)
            ON CONFLICT (source_node_id, target_node_id, relation_type)
            DO UPDATE SET last_confirmed_at = now(),
                          evidence = EXCLUDED.evidence
            RETURNING id, org_id, source_node_id, target_node_id,
                      relation_type, evidence, first_seen_at, last_confirmed_at
            """,
            org_id,
            source_node_id,
            target_node_id,
            relation_type,
            json.dumps(evidence or []),
        )
        return dict(row) if row else {}

    async def link_batch(self, relations: list[dict]) -> int:
        async with self.client.transaction() as conn:
            count = 0
            for rel in relations:
                src = await self.client.fetch_one_conn(
                    conn,
                    """
                    INSERT INTO knowledge_nodes (org_id, node_type, key, title)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (org_id, node_type, key)
                    DO UPDATE SET title = COALESCE(EXCLUDED.title, knowledge_nodes.title)
                    RETURNING id, org_id, node_type, key, title
                    """,
                    rel.get("org_id"),
                    rel.get("source_type", "code"),
                    rel["source"],
                    None,
                )
                tgt = await self.client.fetch_one_conn(
                    conn,
                    """
                    INSERT INTO knowledge_nodes (org_id, node_type, key, title)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (org_id, node_type, key)
                    DO UPDATE SET title = COALESCE(EXCLUDED.title, knowledge_nodes.title)
                    RETURNING id, org_id, node_type, key, title
                    """,
                    rel.get("org_id"),
                    rel.get("target_type", "doc"),
                    rel["target"],
                    None,
                )
                await self.client.fetch_one_conn(
                    conn,
                    """
                    INSERT INTO doc_edges (
                        org_id, source_node_id, target_node_id, relation_type, evidence
                    ) VALUES ($1, $2::UUID, $3::UUID, $4, $5::JSONB)
                    ON CONFLICT (source_node_id, target_node_id, relation_type)
                    DO UPDATE SET last_confirmed_at = now(),
                                  evidence = EXCLUDED.evidence
                    RETURNING id
                    """,
                    rel.get("org_id"),
                    src["id"],
                    tgt["id"],
                    rel["type"],
                    json.dumps(rel.get("evidence") or []),
                )
                count += 1
        return count

    async def docs_for_code(
        self,
        *,
        code_keys: list[str],
        org_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = await self.client.fetch_all(
            """
            WITH RECURSIVE walk AS (
                SELECT kn.id, kn.node_type, kn.key
                FROM knowledge_nodes kn
                WHERE kn.node_type = 'code' AND kn.key = ANY($1::TEXT[])
                  AND ($2::TEXT IS NULL OR kn.org_id = $2)
              UNION
                SELECT t.id, t.node_type, t.key
                FROM doc_edges de
                JOIN walk w ON de.source_node_id = w.id
                JOIN knowledge_nodes t ON t.id = de.target_node_id
                WHERE ($2::TEXT IS NULL OR de.org_id = $2)
            )
            SELECT DISTINCT id AS node_id, node_type, key FROM walk
            WHERE node_type = 'doc'
            """,
            code_keys,
            org_id,
        )
        return [dict(r) for r in rows]
