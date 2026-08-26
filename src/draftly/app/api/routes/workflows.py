"""Workflow event streaming: one-time tickets + SSE bridge (spec §Components).

Browser EventSource cannot set headers, so clients exchange their Clerk
token for a single-use short-lived ticket bound to (org_id, run_id), then
open GET /workflows/{run_id}/events?ticket=... Frames use the envelope's
``type`` as the SSE event name; ``workflow_result`` terminates the stream.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from draftly.app.api.auth import get_verified_token
from draftly.events.stream_envelope import StreamEnvelope
from draftly.integrations.ticket_store import RedisTicketStore

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/workflows", tags=["workflows"])

_DEFAULT_HEARTBEAT_SECONDS = 15.0


def _tickets(request: Request) -> RedisTicketStore:
    store = getattr(request.app.state, "redis_tickets", None)
    if store is None:
        redis_client = getattr(request.app.state, "redis_client", None)
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
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    if str(record.get("org_id") or "") != org_id:
        raise HTTPException(
            status_code=403, detail="Run belongs to another organization"
        )

    return {"ticket": await _tickets(request).issue(run_id, org_id=org_id)}


def _format_envelope(envelope: StreamEnvelope) -> str:
    body = json.dumps(envelope.to_dict(), default=str)
    return f"id: {envelope.seq}\nevent: {envelope.type}\ndata: {body}\n\n"


async def _event_source(
    bus: Any,
    run_id: str,
    heartbeat_seconds: float,
    *,
    replayed: list[dict[str, Any]] | None = None,
    min_live_seq: int = 0,
) -> AsyncIterator[str]:
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
        yield _format_envelope(envelope)
        if envelope.type == "workflow_result":
            replayed_done = True

    # If the workflow already completed before this connection opened,
    # all events were replayed from the store — no live subscription needed.
    if replayed_done:
        return

    gen = bus.subscribe(run_id)
    while True:
        try:
            envelope = await asyncio.wait_for(
                gen.__anext__(), timeout=heartbeat_seconds
            )
        except StopAsyncIteration:
            return
        except TimeoutError:
            yield ": ping\n\n"
            continue

        if envelope.seq <= min_live_seq:
            continue

        yield _format_envelope(envelope)
        if envelope.type == "workflow_result":
            await gen.aclose()
            return


@router.get("/{run_id}/events")
async def stream_events(
    run_id: str,
    ticket: str,
    request: Request,
    heartbeat: float | None = None,
) -> StreamingResponse:
    claimed = await _tickets(request).consume(ticket)
    if claimed is None or claimed[0] != run_id:
        raise HTTPException(status_code=403, detail="Invalid or expired ticket")

    bus = getattr(request.app.state.draftly.workflows, "event_bus", None)
    if bus is None:
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

    # Replay stored events: on initial connection (no Last-Event-ID) replay
    # the full history so the client sees all workflow progress even if the
    # workflow completed before the SSE connection was opened.
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
        except Exception:
            logger.warning("sse_replay_failed run_id=%s", run_id, exc_info=True)

    return StreamingResponse(
        _event_source(
            bus,
            run_id,
            heartbeat_seconds,
            replayed=replayed,
            min_live_seq=min_live_seq,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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
) -> StreamingResponse:
    """SSE stream of dashboard events (review, job, run lifecycle)."""
    claimed = await _tickets(request).consume(ticket)
    if claimed is None:
        raise HTTPException(status_code=403, detail="Invalid or expired ticket")

    _, org_id = claimed
    broadcaster = getattr(request.app.state, "dashboard_broadcaster", None)
    if broadcaster is None:
        raise HTTPException(status_code=503, detail="Dashboard broadcaster unavailable")

    heartbeat_seconds = float(heartbeat if heartbeat and heartbeat > 0 else 15.0)

    async def _dashboard_source():
        gen = broadcaster.subscribe(org_id)
        while True:
            try:
                event = await asyncio.wait_for(
                    gen.__anext__(), timeout=heartbeat_seconds
                )
            except StopAsyncIteration:
                return
            except TimeoutError:
                yield ": ping\n\n"
                continue
            body = json.dumps(event)
            yield f"event: {event.get('type', 'unknown')}\ndata: {body}\n\n"

    return StreamingResponse(
        _dashboard_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
