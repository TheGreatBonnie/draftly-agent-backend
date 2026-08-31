"""DocGraph facade — software <-> documentation relationships (fail-open reads)."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class DocGraphService:
    """Code <-> concept <-> doc relationships for impact resolution."""

    def __init__(self, store: Any = None) -> None:
        from draftly.integrations.database.doc_relations_store import (
            DocRelationsStore,
        )

        self.store = store or DocRelationsStore()

    async def ensure_node(
        self,
        node_type: str,
        key: str,
        *,
        org_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        return await self.store.ensure_node(
            node_type=node_type, key=key, org_id=org_id, title=title
        )

    async def link(
        self,
        source_key: str,
        target_key: str,
        relation_type: str,
        *,
        org_id: str | None = None,
        source_type: str = "code",
        target_type: str = "doc",
        evidence: list | None = None,
    ) -> dict[str, Any]:
        src = await self.ensure_node(source_type, source_key, org_id=org_id)
        tgt = await self.ensure_node(target_type, target_key, org_id=org_id)
        edge = await self.store.upsert_edge(
            source_node_id=src["id"],
            target_node_id=tgt["id"],
            relation_type=relation_type,
            org_id=org_id,
            evidence=evidence,
        )
        logger.debug("doc_edge_linked type=%s", relation_type)
        return edge

    async def link_batch(self, relations: list[dict]) -> int:
        return await self.store.link_batch(relations)

    async def affected_docs(
        self,
        code_paths: list[str],
        *,
        org_id: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            return await self.store.docs_for_code(code_keys=code_paths, org_id=org_id)
        except Exception:
            logger.exception("affected_docs_failed")
            return []
