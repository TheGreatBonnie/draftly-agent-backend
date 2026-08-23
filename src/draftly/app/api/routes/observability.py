"""Cross-cutting telemetry reads: routing decisions, model performance, jobs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from draftly.app.api.auth import get_verified_token

router = APIRouter(tags=["observability"], dependencies=[Depends(get_verified_token)])


def _repos(request: Request) -> Any:
    repos = getattr(request.app.state.draftly.dependencies, "repositories", None)
    if repos is None:
        raise HTTPException(status_code=503, detail="Stores unavailable")
    return repos


@router.get("/observability/routing-decisions")
async def routing_decisions(
    request: Request,
    limit: int = 100,
) -> dict[str, Any]:
    """Recent model-routing decisions with reason codes and actuals."""
    recent = getattr(_repos(request).routing, "recent", None)
    if recent is None:
        raise HTTPException(status_code=503, detail="Routing store unavailable")
    return {"items": await recent(limit=max(1, min(limit, 200)))}


@router.get("/observability/model-performance")
async def model_performance(request: Request) -> dict[str, Any]:
    """Persisted per-task/model EMA aggregates backing quality gates."""
    performance = getattr(_repos(request).performance, "all", None)
    if performance is None:
        raise HTTPException(status_code=503, detail="Performance store unavailable")
    return {"items": await performance()}


@router.get("/jobs")
async def active_jobs(request: Request) -> dict[str, Any]:
    """Currently active background jobs (history: GET /documentation/sync/{job_id})."""
    jobs = getattr(_repos(request).jobs, "list_active", None)
    if jobs is None:
        raise HTTPException(status_code=503, detail="Jobs store unavailable")
    return {"items": await jobs()}
