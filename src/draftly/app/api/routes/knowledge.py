"""Organization-scoped Knowledge read endpoints."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from draftly.app.api.auth import get_verified_token
from draftly.app.api.knowledge_schemas import (
    KnowledgeDetail,
    KnowledgeEmbeddingStats,
    KnowledgeGraph,
    KnowledgePage,
    KnowledgeSearchResponse,
    KnowledgeSourceSummary,
    KnowledgeStats,
    KnowledgeStatus,
    KnowledgeTopics,
)

router = APIRouter(
    prefix="/knowledge",
    tags=["knowledge"],
    dependencies=[Depends(get_verified_token)],
)


def _org_id(token: dict[str, Any]) -> str:
    org_id = str(token.get("org_id") or "").strip()
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return org_id


def _knowledge_repo(request: Request) -> Any:
    try:
        repository = request.app.state.draftly.dependencies.repositories.knowledge
    except AttributeError as exc:
        raise HTTPException(status_code=503, detail="Knowledge store unavailable") from exc
    return repository


@router.get("", response_model=KnowledgePage)
async def list_knowledge(
    request: Request,
    status: KnowledgeStatus | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = None,
    token: dict[str, Any] = Depends(get_verified_token),
) -> KnowledgePage:
    """List the token organization's curated Knowledge items."""
    try:
        result = await _knowledge_repo(request).list_page(
            org_id=_org_id(token),
            status=status.value if status else None,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return KnowledgePage.model_validate(result)


@router.get("/stats", response_model=KnowledgeStats)
async def knowledge_stats(
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> KnowledgeStats:
    """Return database aggregates for the token organization's Knowledge."""
    return KnowledgeStats.model_validate(
        await _knowledge_repo(request).stats(org_id=_org_id(token))
    )


@router.get("/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    request: Request,
    q: str = Query(min_length=1, max_length=300),
    limit: int = Query(default=20, ge=1, le=50),
    status: KnowledgeStatus | None = None,
    token: dict[str, Any] = Depends(get_verified_token),
) -> KnowledgeSearchResponse:
    """Run bounded semantic search over the token organization's Knowledge."""
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Missing query parameter 'q'")
    try:
        items = await _knowledge_repo(request).search(
            org_id=_org_id(token),
            query=query,
            limit=limit,
            status=status.value if status else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return KnowledgeSearchResponse(query=query, items=items, total=len(items))


@router.get("/sources", response_model=list[KnowledgeSourceSummary])
async def knowledge_sources(
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> list[KnowledgeSourceSummary]:
    """Return provenance evidence grouped by source type and repository."""
    return [
        KnowledgeSourceSummary.model_validate(item)
        for item in await _knowledge_repo(request).source_summaries(org_id=_org_id(token))
    ]


@router.get("/graph", response_model=KnowledgeGraph)
async def knowledge_graph(
    request: Request,
    limit_nodes: int = Query(default=100, ge=1, le=200),
    limit_edges: int = Query(default=200, ge=1, le=500),
    token: dict[str, Any] = Depends(get_verified_token),
) -> KnowledgeGraph:
    """Return bounded organization-scoped Knowledge nodes and links."""
    try:
        result = await _knowledge_repo(request).graph(
            org_id=_org_id(token),
            limit_nodes=limit_nodes,
            limit_edges=limit_edges,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return KnowledgeGraph.model_validate(result)


@router.get("/topics", response_model=KnowledgeTopics)
async def knowledge_topics(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    token: dict[str, Any] = Depends(get_verified_token),
) -> KnowledgeTopics:
    """Return persisted topic metadata aggregates."""
    try:
        items = await _knowledge_repo(request).topics(
            org_id=_org_id(token),
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return KnowledgeTopics(items=items)


@router.get("/embeddings", response_model=KnowledgeEmbeddingStats)
async def knowledge_embeddings(
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> KnowledgeEmbeddingStats:
    """Return embedding coverage metadata without exposing vectors."""
    return KnowledgeEmbeddingStats.model_validate(
        await _knowledge_repo(request).embedding_stats(org_id=_org_id(token))
    )


@router.get("/{item_id}", response_model=KnowledgeDetail)
async def get_knowledge_item(
    item_id: str,
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> KnowledgeDetail:
    """Fetch one Knowledge item plus scoped provenance, relations, and feedback."""
    try:
        UUID(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Knowledge item id") from exc
    item = await _knowledge_repo(request).detail(
        org_id=_org_id(token),
        item_id=item_id,
    )
    if item is None:
        raise HTTPException(status_code=404, detail=f"Knowledge {item_id} not found")
    return KnowledgeDetail.model_validate(item)
