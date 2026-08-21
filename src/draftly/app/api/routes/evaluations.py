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
    evaluation_type: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List evaluation runs (plan §9.1)."""
    repo = _evaluations(request)
    items = await repo.search(
        org_id="",
        evaluation_type=evaluation_type,
        limit=max(1, min(limit, 200)),
    )
    return {"items": items}


@router.post("/run")
async def run_evaluations(request: Request) -> dict[str, Any]:
    """Trigger the evaluation loop workflow."""
    application = request.app.state.draftly
    worker = getattr(application, "worker", None)
    if worker is not None and worker.task_runner.has_task("evaluation.loop"):
        result = await worker.run_task("evaluation.loop")
        return {"status": "completed", "result": result}

    # Worker disabled: invoke the workflow directly against the context.
    workflows = getattr(application, "workflows", None)
    registry = getattr(workflows, "registry", None)
    func = registry.get("evaluation_loop") if registry else None
    if func is None:
        raise HTTPException(status_code=503, detail="Runtime not started")
    state = await func(workflows.context)
    return {
        "status": str(getattr(state, "status", "unknown")),
        "run_id": getattr(state, "run_id", None),
    }
