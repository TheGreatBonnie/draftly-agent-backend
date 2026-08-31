"""Knowledge read endpoints — surface curated memory_items to the UI."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from draftly.app.api.auth import get_verified_token
from draftly.integrations.database.memory_feedback_store import MemoryFeedbackStore
from draftly.integrations.database.memory_links_store import MemoryLinksStore
from draftly.integrations.database.memory_sources_store import MemorySourcesStore
from draftly.memory.embeddings import EmbeddingService
from draftly.memory.repository import MemoryNamespaces

router = APIRouter(
    prefix="/knowledge",
    tags=["knowledge"],
    dependencies=[Depends(get_verified_token)],
)

VERIFIED_THRESHOLD = 0.5


def derive_status(record: dict[str, Any]) -> str:
    """Map a memory record to a UI status (verified / needs-verification / stale)."""
    if record.get("status") not in ("active", None):
        return "stale"
    confidence = float(record.get("confidence", 0.0))
    return "verified" if confidence >= VERIFIED_THRESHOLD else "needs-verification"


def _memory_repo(request: Request) -> Any:
    repos = getattr(request.app.state.draftly.dependencies, "repositories", None)
    memory = getattr(repos, "memory", None) if repos else None
    if memory is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    return memory


def _embedder(request: Request) -> Any:
    draftly = getattr(request.app.state, "draftly", None)
    injected = getattr(draftly, "embeddings", None)
    return injected or EmbeddingService()


def _to_list_item(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "entity": record.get("summary") or record.get("content"),
        "description": record.get("summary"),
        "status": derive_status(record),
        "importance": record.get("importance"),
        "confidence": record.get("confidence"),
        "updated_at": record.get("updated_at"),
        "created_at": record.get("created_at"),
        "namespace": record.get("namespace"),
        "memory_type": record.get("memory_type"),
    }


@router.get("")
async def list_knowledge(
    request: Request,
    status: str | None = None,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """List curated knowledge items, optionally filtered by derived status."""
    memory = _memory_repo(request)
    records = await memory.list_namespace(
        namespace=MemoryNamespaces.KNOWLEDGE,
        org_id=token.get("org_id"),
    )
    items = [_to_list_item(r) for r in records]
    if status:
        items = [i for i in items if i["status"] == status]
    return {"items": items}


@router.get("/stats")
async def knowledge_stats(
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Aggregate counts for the knowledge dashboard cards."""
    memory = _memory_repo(request)
    records = await memory.list_namespace(
        namespace=MemoryNamespaces.KNOWLEDGE,
        org_id=token.get("org_id"),
    )
    total = len(records)
    verified = sum(1 for r in records if derive_status(r) == "verified")
    needs = sum(1 for r in records if derive_status(r) == "needs-verification")
    stale = total - verified - needs
    return {
        "total": total,
        "verified": verified,
        "needs_verification": needs,
        "stale": stale,
    }


@router.get("/search")
async def search_knowledge(
    request: Request,
    q: str,
    limit: int = 20,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Semantic search over curated knowledge items."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="Missing query parameter 'q'")
    memory = _memory_repo(request)
    embedder = _embedder(request)
    embedding = await embedder.embed(q)
    results = await memory.semantic_search(
        namespace=MemoryNamespaces.KNOWLEDGE,
        embedding=embedding,
        limit=max(1, min(limit, 100)),
        org_id=token.get("org_id"),
    )
    items = []
    for r in results:
        item = _to_list_item(r)
        item["similarity"] = r.get("similarity")
        items.append(item)
    return {"query": q, "items": items}


@router.get("/{item_id}")
async def get_knowledge_item(
    item_id: str,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Fetch one knowledge item plus its provenance, relations and feedback."""
    memory = _memory_repo(request)
    record = await memory.get(memory_id=item_id)
    org_id = token.get("org_id")
    if (
        record is None
        or record.get("namespace") != MemoryNamespaces.KNOWLEDGE
        or (org_id and record.get("org_id") != org_id)
    ):
        raise HTTPException(status_code=404, detail=f"Knowledge {item_id} not found")

    repos = request.app.state.draftly.dependencies.repositories
    sources_store = getattr(repos, "memory_sources", None) or MemorySourcesStore()
    links_store = getattr(repos, "memory_links", None) or MemoryLinksStore()
    feedback_store = getattr(repos, "memory_feedback", None) or MemoryFeedbackStore()

    sources = await sources_store.list_by_memory(org_id=org_id, memory_item_id=item_id)
    related = await links_store.list_by_memory(org_id=org_id, memory_item_id=item_id)
    feedback = await feedback_store.list_by_memory(org_id=org_id, memory_item_id=item_id)

    return {
        "id": record["id"],
        "entity": record.get("summary") or record.get("content"),
        "description": record.get("content"),
        "status": derive_status(record),
        "importance": record.get("importance"),
        "confidence": record.get("confidence"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "sources": sources,
        "related": related,
        "feedback": feedback,
    }

