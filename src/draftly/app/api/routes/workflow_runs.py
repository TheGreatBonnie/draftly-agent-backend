"""Canonical workflow-run API, including durable actions and SSE access."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sse_starlette import EventSourceResponse, JSONServerSentEvent

from draftly.app.api.auth import get_verified_token, require_workflow_editor
from draftly.app.api.routes.workflows import _event_source, _tickets
from draftly.app.api.workflow_schemas import WorkflowManualRunCreate

router = APIRouter(prefix="/workflow-runs", tags=["workflow-runs"])


def _repositories(request: Request) -> Any:
    return request.app.state.draftly.dependencies.repositories


async def _authorized_run(request: Request, run_id: str, token: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(_repositories(request), "workflow_runs", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Workflow runs unavailable")
    row = await repo.get(org_id=str(token.get("org_id") or ""), run_id=run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return row


async def _dispatch_if_available(
    request: Request, *, definition: dict[str, Any], run: dict[str, Any], token: dict[str, Any]
) -> None:
    """Use the same durable task path as webhooks when the worker is running."""
    application = request.app.state.draftly
    event = {
        "event_id": run["id"],
        "event_type": f"manual.{definition['workflow_key']}",
        "project_id": str(token.get("org_id") or ""),
        "source": "manual",
        "workflow_key": definition["workflow_key"],
        "title": run.get("title"),
        "repository": run.get("repository"),
        "input": run.get("input") or {},
    }
    task_name = f"{definition['workflow_key']}.enqueue"
    settings = getattr(application, "settings", None)
    queues = getattr(application, "rq_queues", None)
    handlers = getattr(application, "task_handlers", None)
    if bool(getattr(settings, "rq_enabled", False)) and queues is not None and handlers is not None:
        from draftly.app.composition.rq_jobs import enqueue_job

        enqueue_job(
            queues=queues,
            task_handlers=handlers,
            task_name=task_name,
            event=event,
            run_id=run["id"],
        )
        return
    worker = getattr(application, "worker", None)
    if worker is not None and getattr(worker, "run_task", None) is not None:
        asyncio.create_task(worker.run_task(task_name, event=event, run_id=run["id"]))


@router.get("")
async def list_runs(
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
    status_filter: str | None = Query(default=None, alias="status"),
    definition_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    repo = getattr(_repositories(request), "workflow_runs", None)
    if repo is None or not hasattr(repo, "list"):
        raise HTTPException(status_code=503, detail="Workflow runs unavailable")
    items, total, next_cursor = await repo.list(
        org_id=str(token.get("org_id") or ""),
        definition_id=definition_id,
        status=status_filter,
        limit=limit,
        cursor=cursor,
    )
    return {"items": items, "total": total, "next_cursor": next_cursor}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_manual_run(
    payload: WorkflowManualRunCreate,
    request: Request,
    token: dict[str, Any] = Depends(require_workflow_editor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    repositories = _repositories(request)
    definitions = getattr(repositories, "workflow_definitions", None)
    runs = getattr(repositories, "workflow_runs", None)
    if definitions is None or runs is None:
        raise HTTPException(status_code=503, detail="Workflow resources unavailable")
    org_id = str(token.get("org_id") or "")
    definition = await definitions.get(org_id=org_id, workflow_id=payload.definition_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if definition.get("status") != "active":
        raise HTTPException(status_code=409, detail="Only active workflows can be run")
    key = idempotency_key or payload.source_event_id
    run = await runs.start_or_get_idempotent(
        org_id=org_id,
        definition_id=payload.definition_id,
        source=payload.source,
        source_event_id=key,
        title=payload.title or definition.get("name"),
        metadata={
            "target": payload.target,
            "input": payload.input,
            "event_type": "manual.workflow",
        },
    )
    if run.get("status") == "queued":
        await _dispatch_if_available(request, definition=definition, run=run, token=token)
    return {"run": run}


@router.get("/{run_id}")
async def get_run(
    run_id: str,
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> dict[str, Any]:
    return {"run": await _authorized_run(request, run_id, token)}


@router.get("/{run_id}/steps")
async def get_run_steps(
    run_id: str,
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> dict[str, Any]:
    await _authorized_run(request, run_id, token)
    return {
        "items": await _repositories(request).workflow_runs.list_steps(
            org_id=str(token.get("org_id") or ""), run_id=run_id
        )
    }


@router.get("/{run_id}/artifacts")
async def get_run_artifacts(
    run_id: str,
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> dict[str, Any]:
    await _authorized_run(request, run_id, token)
    return {
        "items": await _repositories(request).workflow_runs.list_artifacts(
            org_id=str(token.get("org_id") or ""), run_id=run_id
        )
    }


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    request: Request,
    token: dict[str, Any] = Depends(require_workflow_editor),
) -> dict[str, Any]:
    run = await _authorized_run(request, run_id, token)
    if run.get("status") in {"completed", "failed", "cancelled", "skipped"}:
        raise HTTPException(status_code=409, detail="Run is already terminal")
    await _repositories(request).workflow_runs.update_state(
        org_id=str(token.get("org_id") or ""), run_id=run_id, status="cancelled"
    )
    return {"run": {**run, "status": "cancelled"}}


@router.post("/{run_id}/retry", status_code=status.HTTP_201_CREATED)
async def retry_run(
    run_id: str,
    request: Request,
    token: dict[str, Any] = Depends(require_workflow_editor),
) -> dict[str, Any]:
    run = await _authorized_run(request, run_id, token)
    if run.get("status") not in {"failed", "cancelled", "skipped"}:
        raise HTTPException(
            status_code=409, detail="Only failed, cancelled, or skipped runs can be retried"
        )
    definition_id = run.get("definition_id")
    if not definition_id:
        raise HTTPException(status_code=409, detail="Run has no workflow definition")
    definition = await _repositories(request).workflow_definitions.get(
        org_id=str(token.get("org_id") or ""), workflow_id=definition_id
    )
    if definition is None or definition.get("status") != "active":
        raise HTTPException(status_code=409, detail="Workflow is not active")
    retry = await _repositories(request).workflow_runs.start_or_get_idempotent(
        org_id=str(token.get("org_id") or ""),
        definition_id=definition_id,
        source="retry",
        source_event_id=f"{run_id}:retry",
        title=run.get("title"),
        metadata={
            "target": run.get("target"),
            "input": run.get("input"),
            "event_type": "manual.retry",
        },
    )
    await _dispatch_if_available(request, definition=definition, run=retry, token=token)
    return {"run": retry}


@router.post("/{run_id}/stream-ticket")
async def issue_run_ticket(
    run_id: str,
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> dict[str, str]:
    await _authorized_run(request, run_id, token)
    return {"ticket": await _tickets(request).issue(run_id, org_id=str(token.get("org_id") or ""))}


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: str,
    ticket: str,
    request: Request,
    heartbeat: float | None = None,
) -> EventSourceResponse:
    claimed = await _tickets(request).consume(ticket)
    if claimed is None or claimed[0] != run_id:
        raise HTTPException(status_code=403, detail="Invalid or expired ticket")
    bus = getattr(request.app.state.draftly.workflows, "event_bus", None)
    if bus is None:
        raise HTTPException(status_code=503, detail="Event bus unavailable")
    repo = getattr(_repositories(request), "workflow_events", None)
    replayed = await repo.list_after(run_id, seq=0) if repo is not None else []
    min_seq = max((int(row.get("seq") or 0) for row in replayed), default=0)
    source = _event_source(bus, run_id, replayed=replayed, min_live_seq=min_seq)

    async def frames():
        async for data in source:
            yield JSONServerSentEvent(
                data=data, event=data.get("type", "message"), id=str(data.get("seq", ""))
            )

    return EventSourceResponse(
        frames(),
        ping=max(1, int(heartbeat or 15)),
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
