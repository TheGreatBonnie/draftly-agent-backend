"""Run audit trail API over agent_runs/agent_steps (spec §Observability surface)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from draftly.app.api.agent_schemas import RunListResponse, RunStepsResponse
from draftly.app.api.auth import get_verified_token

router = APIRouter(
    prefix="/runs", tags=["runs"], dependencies=[Depends(get_verified_token)]
)


def _repo(request: Request) -> Any:
    repo = getattr(
        getattr(request.app.state.draftly.dependencies, "repositories", None),
        "agent_runs",
        None,
    )
    if repo is None:
        raise HTTPException(status_code=503, detail="Store unavailable")
    return repo


@router.get("", response_model=RunListResponse)
async def list_runs(
    request: Request,
    token: dict = Depends(get_verified_token),
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List audit runs for the caller's organization."""
    items = await _repo(request).list_runs(
        org_id=str(token.get("org_id") or ""),
        status=status,
        limit=max(1, min(limit, 200)),
    )
    return {"items": items}


async def _authorized_run(request: Request, run_id: str, token: dict) -> dict[str, Any]:
    run = await _repo(request).get_run(run_id)
    if run is None or str(run.get("org_id")) != str(token.get("org_id")):
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/{run_id}")
async def get_run(
    run_id: str,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Fetch one audit run by id (org-scoped)."""
    return {"run": await _authorized_run(request, run_id, token)}


@router.get("/{run_id}/steps", response_model=RunStepsResponse)
async def list_run_steps(
    run_id: str,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Ordered agent/tool steps for one run."""
    await _authorized_run(request, run_id, token)
    steps = await _repo(request).list_steps(run_id)
    return {"items": steps}
