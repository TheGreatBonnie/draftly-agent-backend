"""Curator tools writing doc-graph + procedural memory."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from strands.tools import tool

if TYPE_CHECKING:
    from draftly.memory.docgraph.service import DocGraphService
    from draftly.memory.procedural.service import ProceduralService


def _docgraph_service() -> DocGraphService:
    from draftly.memory.docgraph.service import DocGraphService

    return DocGraphService()


def _procedural_service() -> ProceduralService:
    from draftly.memory.procedural.service import ProceduralService

    return ProceduralService()


@tool
async def record_doc_relation(
    source_key: str, target_key: str, relation_type: str, org_id: str
) -> str:
    """Record a relationship between code and documentation in the knowledge graph.

    Args:
        source_key: Source node key (e.g. code path 'auth/token_service.py').
        target_key: Target node key (e.g. doc path or concept name).
        relation_type: One of IMPLEMENTS, DOCUMENTED_BY, AFFECTS, DERIVED_FROM.
        org_id: Organization scope.
    """
    graph = _docgraph_service()
    await graph.link(source_key, target_key, relation_type, org_id=org_id)
    return json.dumps({"linked": True})


@tool
async def record_procedure(name: str, pattern_description: str, org_id: str) -> str:
    """Store a learned investigation playbook for reuse in future runs.

    Args:
        name: Short stable identifier for the procedure.
        pattern_description: When this procedure applies and what steps to take.
        org_id: Organization scope.
    """
    svc = _procedural_service()
    proc = await svc.create(name, pattern_description, org_id=org_id)
    return json.dumps({"procedure_id": proc["id"]})
