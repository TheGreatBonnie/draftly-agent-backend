# app/api/routes/evaluations.py

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from draftly.app.api.auth import get_verified_token

router = APIRouter(
    prefix="/evaluations",
    tags=["evaluations"],
    dependencies=[Depends(get_verified_token)],
)


def _evaluations(request: Request) -> Any:
    application = request.app.state.draftly
    repo = getattr(
        getattr(application.dependencies, "repositories", None),
        "evaluations",
        None,
    )
    if repo is None:
        raise HTTPException(status_code=503, detail="Store unavailable")
    return repo


@router.get("")
async def list_evaluations(
    request: Request,
    token: dict = Depends(get_verified_token),
    evaluation_type: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List evaluation runs for the caller's organization."""
    repo = _evaluations(request)
    items = await repo.search(
        org_id=str(token.get("org_id") or ""),
        evaluation_type=evaluation_type,
        limit=max(1, min(limit, 200)),
    )
    return {"items": items}


@router.get("/{evaluation_id}")
async def get_evaluation(
    evaluation_id: str,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Fetch a single evaluation run for the caller's organization."""
    repo = _evaluations(request)
    item = await repo.get(evaluation_id=evaluation_id)
    if item is None or str(item.get("org_id") or "") != str(
        token.get("org_id") or ""
    ):
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return {"item": item}


@router.post("/run")
async def run_evaluations(
    request: Request,
    token: dict[str, str] = Depends(get_verified_token),
) -> dict[str, Any]:
    """Trigger the evaluation loop workflow."""
    from uuid import uuid4

    org_id = str(token.get("org_id") or "")
    run_id = str(uuid4())
    application = request.app.state.draftly
    worker = getattr(application, "worker", None)
    if worker is not None and worker.task_runner.has_task("evaluation.loop"):
        result = await worker.run_task(
            "evaluation.loop", org_id=org_id, run_id=run_id
        )
        return {"status": "completed", "result": result, "run_id": run_id}

    # Worker disabled: invoke the workflow directly against the context.
    workflows = getattr(application, "workflows", None)
    registry = getattr(workflows, "registry", None)
    func = registry.get("evaluation_loop") if registry else None
    if workflows is None or func is None:
        raise HTTPException(status_code=503, detail="Runtime not started")
    state = await func(workflows.context, org_id=org_id, run_id=run_id)
    return {
        "status": str(getattr(state, "status", "unknown")),
        "run_id": getattr(state, "run_id", None),
    }
