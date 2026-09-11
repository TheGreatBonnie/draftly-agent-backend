"""Workflow event streaming: one-time tickets + SSE bridge (spec §Components).

Browser EventSource cannot set headers, so clients exchange their Clerk
token for a single-use short-lived ticket bound to (org_id, run_id), then
open GET /workflows/{run_id}/events?ticket=... Frames use the envelope's
``type`` as the SSE event name; ``workflow_result`` terminates the stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sse_starlette import EventSourceResponse, JSONServerSentEvent

from draftly.app.api.auth import get_verified_token, require_workflow_editor
from draftly.app.api.workflow_schemas import (
    WorkflowDefinitionCreate,
    WorkflowDefinitionPatch,
    WorkflowRunCreate,
)
from draftly.events.stream_envelope import StreamEnvelope
from draftly.integrations.ticket_store import RedisTicketStore

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/workflows", tags=["workflows"])

_DEFAULT_HEARTBEAT_SECONDS = 15.0


def _tickets(request: Request) -> RedisTicketStore:
    store = getattr(request.app.state, "redis_tickets", None)
    if store is None:
        # Redis client lives on DraftlyApplication (app.state.draftly), not app.state directly.
        draftly = getattr(request.app.state, "draftly", None)
        redis_client = getattr(draftly, "redis_client", None) if draftly is not None else None
        if redis_client is None:
            raise HTTPException(status_code=503, detail="Redis unavailable for tickets")
        store = RedisTicketStore(redis_client.native, ttl_seconds=60)
        request.app.state.redis_tickets = store
    return store


@router.get("")
async def list_workflows(
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
    status_filter: str | None = Query(default=None, alias="status"),
    workflow_key: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    days: int = 30,
) -> dict[str, Any]:
    """List reusable definitions, with a compatibility fallback for old clients."""
    org_id = str(token.get("org_id") or "")
    deps = request.app.state.draftly.dependencies
    definitions = getattr(deps.repositories, "workflow_definitions", None)
    if definitions is not None:
        items, total, next_cursor = await definitions.list(
            org_id=org_id,
            status=status_filter,
            workflow_key=workflow_key,
            limit=limit,
            cursor=cursor,
        )
        return {
            "items": items,
            "summary": await definitions.summary(org_id=org_id, days=days),
            "total": total,
            "next_cursor": next_cursor,
        }

    # Older test/application compositions do not have the canonical repository
    # yet. Preserve their run-shaped response while they migrate.
    db = deps.integrations.database
    from draftly.persistence.repositories.github import list_github_workflows_record

    rows = await list_github_workflows_record(org_id=org_id, db=db)
    return {"items": rows}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workflow_definition(
    payload: WorkflowDefinitionCreate,
    request: Request,
    token: dict[str, Any] = Depends(require_workflow_editor),
) -> dict[str, Any]:
    """Create a draft definition owned by the verified organization."""
    repo = getattr(
        request.app.state.draftly.dependencies.repositories, "workflow_definitions", None
    )
    if repo is None:
        raise HTTPException(status_code=503, detail="Workflow definitions unavailable")
    row = await repo.create(
        org_id=str(token.get("org_id") or ""),
        created_by=str(token.get("user_id") or token.get("sub") or "") or None,
        payload=payload,
    )
    return {"workflow": row}


@router.post("/{workflow_id}/runs", status_code=status.HTTP_201_CREATED)
async def create_definition_run(
    workflow_id: str,
    payload: WorkflowRunCreate,
    request: Request,
    token: dict[str, Any] = Depends(require_workflow_editor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Create and dispatch a run for a specific active definition."""
    repositories = request.app.state.draftly.dependencies.repositories
    definitions = getattr(repositories, "workflow_definitions", None)
    runs = getattr(repositories, "workflow_runs", None)
    if definitions is None or runs is None:
        raise HTTPException(status_code=503, detail="Workflow resources unavailable")
    org_id = str(token.get("org_id") or "")
    definition = await definitions.get(org_id=org_id, workflow_id=workflow_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if definition.get("status") != "active":
        raise HTTPException(status_code=409, detail="Only active workflows can be run")
    run = await runs.start_or_get_idempotent(
        org_id=org_id,
        definition_id=workflow_id,
        source=payload.source,
        source_event_id=idempotency_key or payload.source_event_id,
        title=payload.title or definition.get("name"),
        metadata={
            "event_type": "manual.workflow",
            "target": payload.target,
            "input": payload.input,
        },
    )
    from draftly.app.api.routes.workflow_runs import _dispatch_if_available

    await _dispatch_if_available(request, definition=definition, run=run, token=token)
    return {"run": run}


@router.get("/runs")
async def list_workflow_runs_compat(
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> dict[str, Any]:
    """Deprecated run-shaped list retained for clients before canonical runs."""
    deps = request.app.state.draftly.dependencies
    db = deps.integrations.database
    from draftly.persistence.repositories.github import list_github_workflows_record

    return {
        "items": await list_github_workflows_record(org_id=str(token.get("org_id") or ""), db=db)
    }


@router.post("/{run_id}/stream-ticket")
async def issue_ticket(
    run_id: str,
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> dict[str, Any]:
    """Exchange a Clerk token for a single-use SSE ticket."""
    org_id = str(token.get("org_id") or "")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    repositories = request.app.state.draftly.dependencies.repositories
    jobs = getattr(repositories, "jobs", None)
    if jobs is None:
        raise HTTPException(status_code=503, detail="Jobs store unavailable")

    record = await jobs.get(job_id=run_id)
    if record is None:
        logger.error("stream_ticket_unknown_run", run_id=run_id, org_id=org_id)
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    if str(record.get("org_id") or "") != org_id:
        raise HTTPException(status_code=403, detail="Run belongs to another organization")

    return {"ticket": await _tickets(request).issue(run_id, org_id=org_id)}


@router.get("/{workflow_id}")
async def get_workflow_definition(
    workflow_id: str,
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
    runs_limit: int = 20,
    runs_cursor: str | None = None,
) -> dict[str, Any]:
    """Fetch one org-owned definition and its recent canonical runs."""
    repositories = request.app.state.draftly.dependencies.repositories
    definitions = getattr(repositories, "workflow_definitions", None)
    runs = getattr(repositories, "workflow_runs", None)
    if definitions is None:
        raise HTTPException(status_code=503, detail="Workflow definitions unavailable")
    org_id = str(token.get("org_id") or "")
    definition = await definitions.get(org_id=org_id, workflow_id=workflow_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    recent = {"items": [], "total": 0, "next_cursor": None}
    if runs is not None:
        items, total, next_cursor = await runs.list_for_definition(
            org_id=org_id,
            definition_id=workflow_id,
            limit=runs_limit,
            cursor=runs_cursor,
        )
        recent = {"items": items, "total": total, "next_cursor": next_cursor}
    return {"workflow": definition, "recent_runs": recent}


@router.patch("/{workflow_id}")
async def update_workflow_definition(
    workflow_id: str,
    payload: WorkflowDefinitionPatch,
    request: Request,
    token: dict[str, Any] = Depends(require_workflow_editor),
) -> dict[str, Any]:
    repositories = request.app.state.draftly.dependencies.repositories
    definitions = getattr(repositories, "workflow_definitions", None)
    if definitions is None:
        raise HTTPException(status_code=503, detail="Workflow definitions unavailable")
    row = await definitions.update(
        org_id=str(token.get("org_id") or ""), workflow_id=workflow_id, payload=payload
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"workflow": row}


async def _set_workflow_status(
    workflow_id: str,
    request: Request,
    token: dict[str, Any],
    new_status: str,
) -> dict[str, Any]:
    definitions = getattr(
        request.app.state.draftly.dependencies.repositories, "workflow_definitions", None
    )
    if definitions is None:
        raise HTTPException(status_code=503, detail="Workflow definitions unavailable")
    row = await definitions.set_status(
        org_id=str(token.get("org_id") or ""), workflow_id=workflow_id, status=new_status
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"workflow": row}


@router.post("/{workflow_id}/pause")
async def pause_workflow(
    workflow_id: str,
    request: Request,
    token: dict[str, Any] = Depends(require_workflow_editor),
) -> dict[str, Any]:
    return await _set_workflow_status(workflow_id, request, token, "paused")


@router.post("/{workflow_id}/resume")
async def resume_workflow(
    workflow_id: str,
    request: Request,
    token: dict[str, Any] = Depends(require_workflow_editor),
) -> dict[str, Any]:
    return await _set_workflow_status(workflow_id, request, token, "active")


async def _event_source(
    bus: Any,
    run_id: str,
    *,
    replayed: list[dict[str, Any]] | None = None,
    min_live_seq: int = 0,
) -> AsyncIterator[dict]:
    """Yield event dicts for sse-starlette wrapping."""
    logger.debug(
        "sse_event_source_start",
        run_id=run_id,
        replayed_count=len(replayed or []),
        min_live_seq=min_live_seq,
    )  # noqa: E501
    replayed_done = False
    for row in replayed or []:
        envelope = StreamEnvelope(
            type=str(row.get("type", "unknown")),
            run_id=run_id,
            surface=str(row.get("surface", "")),
            seq=int(row.get("seq", 0)),
            ts=str(row.get("ts") or ""),
            node_id=row.get("node_id"),
            payload=dict(row.get("payload") or {}),
        )
        logger.debug("sse_replay_yield", run_id=run_id, type=envelope.type, seq=envelope.seq)
        yield envelope.to_dict()
        if envelope.type == "workflow_result":
            replayed_done = True

    if replayed_done:
        logger.debug("sse_replay_done_early", run_id=run_id)
        return

    queue: asyncio.Queue[StreamEnvelope | None] = asyncio.Queue()
    sentinel = None

    async def _pump() -> None:
        logger.debug("sse_pump_start", run_id=run_id)
        try:
            async for envelope in bus.subscribe(run_id):
                logger.debug(
                    "sse_pump_envelope", run_id=run_id, type=envelope.type, seq=envelope.seq
                )  # noqa: E501
                await queue.put(envelope)
        except asyncio.CancelledError:
            logger.debug("sse_pump_cancelled", run_id=run_id)
            pass
        except Exception:
            logger.exception("sse_pump_error", run_id=run_id)
        finally:
            logger.debug("sse_pump_sentinel", run_id=run_id)
            await queue.put(sentinel)

    pump_task = asyncio.create_task(_pump())
    try:
        while True:
            try:
                envelope = await asyncio.wait_for(queue.get(), timeout=15.0)
            except TimeoutError:
                logger.debug("sse_queue_timeout", run_id=run_id)
                continue

            if envelope is sentinel:
                logger.debug("sse_sentinel_received", run_id=run_id)
                return

            if envelope.seq <= min_live_seq:
                logger.debug(
                    "sse_skip_old_seq", run_id=run_id, seq=envelope.seq, min_live_seq=min_live_seq
                )  # noqa: E501
                continue

            logger.debug("sse_yield", run_id=run_id, type=envelope.type, seq=envelope.seq)
            yield envelope.to_dict()
            if envelope.type == "workflow_result":
                logger.debug("sse_workflow_result_end", run_id=run_id)
                return
    finally:
        pump_task.cancel()
        try:
            await pump_task
        except asyncio.CancelledError:
            pass


@router.get("/{run_id}/events")
async def stream_events(
    run_id: str,
    ticket: str,
    request: Request,
    heartbeat: float | None = None,
) -> EventSourceResponse:
    logger.info("sse_stream_start", run_id=run_id)
    claimed = await _tickets(request).consume(ticket)
    if claimed is None or claimed[0] != run_id:
        logger.warning("sse_invalid_ticket", run_id=run_id)
        raise HTTPException(status_code=403, detail="Invalid or expired ticket")

    bus = getattr(request.app.state.draftly.workflows, "event_bus", None)
    if bus is None:
        logger.error("sse_no_event_bus", run_id=run_id)
        raise HTTPException(status_code=503, detail="Event bus unavailable")

    heartbeat_seconds = float(
        heartbeat
        if heartbeat is not None and heartbeat > 0
        else getattr(
            request.app.state,
            "heartbeat",
            _DEFAULT_HEARTBEAT_SECONDS,
        )
    )

    replayed: list[dict[str, Any]] = []
    min_live_seq = 0
    last_event_id = request.headers.get("last-event-id", "")

    events_repo = getattr(
        getattr(request.app.state.draftly.dependencies, "repositories", None),
        "workflow_events",
        None,
    )
    if events_repo is not None:
        try:
            if last_event_id.isdigit():
                min_live_seq = int(last_event_id)
                replayed = await events_repo.list_after(run_id, seq=min_live_seq)
            else:
                replayed = await events_repo.list_after(run_id, seq=0)
            logger.info(
                "sse_replay_loaded", run_id=run_id, count=len(replayed), min_live_seq=min_live_seq
            )  # noqa: E501
        except Exception:
            logger.warning("sse_replay_failed run_id=%s", run_id, exc_info=True)
    else:
        logger.warning("sse_no_workflow_events_repo", run_id=run_id)

    if replayed and not last_event_id.isdigit():
        min_live_seq = max(int(r.get("seq") or 0) for r in replayed)

    seen: set[int] = set()
    deduped = []
    for row in replayed:
        seq = int(row.get("seq") or 0)
        if seq in seen:
            continue
        seen.add(seq)
        deduped.append(row)
    replayed = deduped

    gen = _event_source(bus, run_id, replayed=replayed, min_live_seq=min_live_seq)

    async def sse_generator():
        logger.debug("sse_generator_start", run_id=run_id)
        async for data in gen:
            logger.debug(
                "sse_frame_send", run_id=run_id, type=data.get("type"), seq=data.get("seq")
            )  # noqa: E501
            yield JSONServerSentEvent(
                data=data,
                event=data.get("type", "message"),
                id=str(data.get("seq", "")),
            )
        logger.debug("sse_generator_end", run_id=run_id)

    return EventSourceResponse(
        sse_generator(),
        ping=max(1, int(heartbeat_seconds)),
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ============================================================
# Dashboard SSE Endpoint
# ============================================================

_DASHBOARD_TICKET_SENTINEL = "_dashboard_"


@router.post("/dashboard-ticket")
async def issue_dashboard_ticket(
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> dict[str, Any]:
    """Exchange a Clerk token for a single-use dashboard SSE ticket."""
    org_id = str(token.get("org_id") or "")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    ticket = await _tickets(request).issue(_DASHBOARD_TICKET_SENTINEL, org_id=org_id)
    return {"ticket": ticket}


@router.get("/events/dashboard")
async def stream_dashboard_events(
    ticket: str,
    request: Request,
    heartbeat: float | None = None,
) -> EventSourceResponse:
    """SSE stream of dashboard events (review, job, run lifecycle)."""
    claimed = await _tickets(request).consume(ticket)
    if claimed is None:
        raise HTTPException(status_code=403, detail="Invalid or expired ticket")

    _, org_id = claimed
    broadcaster = getattr(request.app.state, "dashboard_broadcaster", None)
    if broadcaster is None:
        raise HTTPException(status_code=503, detail="Dashboard broadcaster unavailable")

    heartbeat_seconds = float(
        heartbeat
        if heartbeat is not None and heartbeat > 0
        else getattr(
            request.app.state,
            "heartbeat",
            _DEFAULT_HEARTBEAT_SECONDS,
        )
    )

    last_event_id = request.headers.get("last-event-id", "")

    async def _dashboard_source():
        gen = broadcaster.subscribe(org_id, last_id=str(last_event_id) if last_event_id else "0")
        while True:
            try:
                event = await asyncio.wait_for(gen.__anext__(), timeout=heartbeat_seconds)
            except StopAsyncIteration:
                return
            except TimeoutError:
                await asyncio.sleep(0.1)
                continue
            yield JSONServerSentEvent(
                data={"type": event.get("type", "message"), "payload": event.get("payload", {})},
                event=event.get("type", "message"),
                id=str(event.get("id", "")),
            )

    return EventSourceResponse(
        _dashboard_source(),
        ping=max(1, int(heartbeat_seconds)),
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
