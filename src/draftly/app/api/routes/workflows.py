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
from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette import EventSourceResponse, JSONServerSentEvent

from draftly.app.api.auth import get_verified_token
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
        raise HTTPException(
            status_code=403, detail="Run belongs to another organization"
        )

    return {"ticket": await _tickets(request).issue(run_id, org_id=org_id)}


async def _event_source(
    bus: Any,
    run_id: str,
    *,
    replayed: list[dict[str, Any]] | None = None,
    min_live_seq: int = 0,
) -> AsyncIterator[dict]:
    """Yield event dicts for sse-starlette wrapping."""
    logger.debug("sse_event_source_start", run_id=run_id, replayed_count=len(replayed or []), min_live_seq=min_live_seq)
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
                logger.debug("sse_pump_envelope", run_id=run_id, type=envelope.type, seq=envelope.seq)
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
                logger.debug("sse_skip_old_seq", run_id=run_id, seq=envelope.seq, min_live_seq=min_live_seq)
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
            logger.info("sse_replay_loaded", run_id=run_id, count=len(replayed), min_live_seq=min_live_seq)
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
            logger.debug("sse_frame_send", run_id=run_id, type=data.get("type"), seq=data.get("seq"))
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

    async def _dashboard_source():
        gen = broadcaster.subscribe(org_id)
        while True:
            try:
                event = await asyncio.wait_for(gen.__anext__(), timeout=heartbeat_seconds)
            except StopAsyncIteration:
                return
            except TimeoutError:
                await asyncio.sleep(0.1)
                continue
            yield JSONServerSentEvent(
                data=event,
                event=event.get("type", "message"),
            )

    return EventSourceResponse(
        _dashboard_source(),
        ping=max(1, int(heartbeat_seconds)),
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
