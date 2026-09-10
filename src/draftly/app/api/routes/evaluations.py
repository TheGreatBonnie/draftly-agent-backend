# app/api/routes/evaluations.py

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from draftly.app.api.auth import get_verified_token
from draftly.app.api.evaluation_schemas import (
    CursorPage,
    EvaluationAggregateSummary,
    EvaluationCaseResult,
    EvaluationCatalog,
    EvaluationRunDetail,
    EvaluationRunRequest,
    EvaluationRunSummary,
)

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


def _summary_payload(item: dict[str, Any]) -> dict[str, Any]:
    metrics = item.get("metrics") or {}
    raw_status = str(item.get("status") or "failed").lower()
    status = raw_status if raw_status in {"queued", "running", "cancelled", "skipped"} else (
        "passed" if item.get("passed") else "failed"
    )
    started_at = item.get("started_at")
    completed_at = item.get("completed_at")
    duration_ms = None
    if started_at is not None and completed_at is not None:
        if isinstance(started_at, str):
            started_at = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if isinstance(completed_at, str):
            completed_at = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        duration_ms = max(0, round((completed_at - started_at).total_seconds() * 1000))
    datasets = list(dict.fromkeys(
        [str(row.get("dataset")) for row in metrics.get("granular", []) if row.get("dataset")]
        + [str(value) for value in metrics.get("evaluation_types", [])]
    ))
    return EvaluationRunSummary.model_validate(
        {
            "id": str(item.get("id") or ""),
            "run_id": str(item.get("run_id") or item.get("id") or ""),
            "name": str(item.get("name") or item.get("target_type") or "Evaluation run"),
            "evaluation_type": str(item.get("evaluation_type") or "documentation"),
            "datasets": datasets,
            "cases": int(metrics.get("cases") or 0),
            "passed": int(metrics.get("passed") or 0),
            "failed": int(metrics.get("failed") or 0),
            "score": item.get("score"),
            "status": status,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": duration_ms,
        }
    ).model_dump(mode="json")


def _case_payload(item: dict[str, Any]) -> dict[str, Any]:
    return EvaluationCaseResult.model_validate(
        {
            **item,
            "id": str(item.get("id") or ""),
            "evaluation_id": str(item.get("evaluation_id") or ""),
            "run_id": str(item.get("run_id") or ""),
            "passed": bool(item.get("passed", item.get("test_pass", False))),
            "evidence": item.get("evidence") or [],
        }
    ).model_dump(mode="json")


def _request_hash(payload: EvaluationRunRequest) -> str:
    encoded = json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evaluation_environment(request: Request) -> str:
    application = getattr(request.app.state, "draftly", None)
    settings = getattr(application, "settings", None)
    return str(getattr(settings, "environment", "development")).lower()


@router.get("")
async def list_evaluations(
    request: Request,
    token: dict = Depends(get_verified_token),
    evaluation_type: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """List evaluation runs for the caller's organization."""
    repo = _evaluations(request)
    org_id = str(token.get("org_id") or "")
    if hasattr(repo, "list_runs"):
        items, total, next_cursor = await repo.list_runs(
            org_id=org_id,
            evaluation_type=evaluation_type,
            limit=max(1, min(limit, 200)),
            cursor=cursor,
        )
        return {
            "items": [_summary_payload(item) for item in items],
            "total": total,
            "next_cursor": next_cursor,
        }
    items = await repo.search(
        org_id=org_id,
        evaluation_type=evaluation_type,
        limit=max(1, min(limit, 200)),
    )
    return {"items": items, "total": len(items), "next_cursor": None}


@router.get("/summary")
async def evaluation_summary(
    request: Request,
    token: dict = Depends(get_verified_token),
    days: int = 30,
) -> dict[str, Any]:
    if days not in {1, 7, 14, 30}:
        raise HTTPException(status_code=422, detail="days must be one of 1, 7, 14, 30")
    result = await _evaluations(request).aggregate_summary(
        org_id=str(token.get("org_id") or ""), days=days
    )
    return EvaluationAggregateSummary.model_validate(result).model_dump(mode="json")


@router.get("/catalog")
async def evaluation_catalog(request: Request) -> dict[str, Any]:
    return EvaluationCatalog.model_validate(_evaluations(request).catalog()).model_dump(mode="json")


@router.get("/runs/{run_id}/cases")
async def list_evaluation_cases(
    run_id: str,
    request: Request,
    token: dict = Depends(get_verified_token),
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    repo = _evaluations(request)
    if await repo.get_run_summary(
        org_id=str(token.get("org_id") or ""), run_id=run_id
    ) is None:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    items, next_cursor = await repo.list_case_results(
        org_id=str(token.get("org_id") or ""),
        run_id=run_id,
        cursor=cursor,
        limit=max(1, min(limit, 200)),
    )
    page = CursorPage[EvaluationCaseResult](
        items=[EvaluationCaseResult.model_validate(item) for item in items],
        total=len(items),
        next_cursor=next_cursor,
    )
    return page.model_dump(mode="json")


@router.get("/runs/{run_id}")
async def get_evaluation_run(
    run_id: str,
    request: Request,
    token: dict = Depends(get_verified_token),
    cases_limit: int = 50,
    cases_cursor: str | None = None,
) -> dict[str, Any]:
    detail = await _evaluations(request).get_run_detail(
        org_id=str(token.get("org_id") or ""),
        run_id=run_id,
        cases_limit=max(1, min(cases_limit, 200)),
        cases_cursor=cases_cursor,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    payload = {
        **detail,
        "summary": _summary_payload(detail["summary"]),
        "cases": [_case_payload(item) for item in detail.get("cases", [])],
    }
    return EvaluationRunDetail.model_validate(payload).model_dump(mode="json")


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
    payload: EvaluationRunRequest = Body(default_factory=EvaluationRunRequest),
    token: dict[str, str] = Depends(get_verified_token),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    live: bool | None = None,
) -> dict[str, Any]:
    """Trigger the evaluation loop workflow.

    Production requests are queued and deduplicated by organization plus
    ``Idempotency-Key``. Development keeps the legacy inline execution path.
    """
    from uuid import uuid4

    org_id = str(token.get("org_id") or "")
    requested_live = payload.live if live is None else live
    environment = _evaluation_environment(request)
    if environment == "production" and not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    idempotency_key = idempotency_key or f"legacy-{uuid4()}"
    try:
        repo = _evaluations(request)
    except (HTTPException, AttributeError):
        repo = None
    application = request.app.state.draftly
    worker = getattr(application, "worker", None)
    idempotency = getattr(repo, "idempotency_store", None)
    if idempotency is not None:
        request_hash = _request_hash(payload)
        existing = await idempotency.get(org_id=org_id, idempotency_key=idempotency_key)
        if existing is not None:
            if existing.get("request_hash") != request_hash:
                raise HTTPException(status_code=409, detail="Idempotency-Key request mismatch")
            return existing.get("response") or {}
    else:
        request_hash = _request_hash(payload)
    if environment == "production":
        if idempotency is None:
            raise HTTPException(status_code=503, detail="Evaluation persistence unavailable")
        if worker is None or not worker.task_runner.has_task("evaluation.loop"):
            raise HTTPException(status_code=503, detail="Evaluation queue unavailable")
    run_id = str(uuid4())
    response = {
        "run_id": run_id,
        "status": "queued",
        "stream_ticket": None,
    }
    if idempotency is not None:
        await idempotency.create(
            org_id=org_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            run_id=run_id,
            response=response,
        )
    if worker is not None and worker.task_runner.has_task("evaluation.loop"):
        if environment == "production":
            import asyncio

            asyncio.create_task(
                worker.run_task(
                    "evaluation.loop",
                    org_id=org_id,
                    run_id=run_id,
                    live=requested_live,
                    datasets=payload.datasets,
                )
            )
            return JSONResponse(status_code=202, content=response)
        result = await worker.run_task(
            "evaluation.loop", org_id=org_id, run_id=run_id, live=requested_live,
            datasets=payload.datasets,
        )
        return {"status": "completed", "result": result, "run_id": run_id}

    # Worker disabled: invoke the workflow directly against the context.
    workflows = getattr(application, "workflows", None)
    registry = getattr(workflows, "registry", None)
    func = registry.get("evaluation_loop") if registry else None
    if workflows is None or func is None:
        raise HTTPException(status_code=503, detail="Runtime not started")
    state = await func(
        workflows.context,
        org_id=org_id,
        run_id=run_id,
        live=requested_live,
        datasets=payload.datasets,
    )
    return {
        "status": str(getattr(state, "status", "unknown")),
        "run_id": getattr(state, "run_id", None),
    }
