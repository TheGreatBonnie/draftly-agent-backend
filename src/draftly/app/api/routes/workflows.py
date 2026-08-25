"""Workflow event streaming: one-time tickets + SSE bridge (spec §Components).

Browser EventSource cannot set headers, so clients exchange their Clerk
token for a single-use short-lived ticket bound to (org_id, run_id), then
open GET /workflows/{run_id}/events?ticket=... Frames use the envelope's
``type`` as the SSE event name; ``workflow_result`` terminates the stream.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from draftly.app.api.auth import get_verified_token
from draftly.events.stream_envelope import StreamEnvelope

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/workflows", tags=["workflows"])

_DEFAULT_HEARTBEAT_SECONDS = 15.0


class TicketStore:
    """Single-use, TTL-bound stream tickets (in-process)."""

    def __init__(self, ttl_seconds: int = 60) -> None:
        self._ttl = ttl_seconds
        self._tickets: dict[str, tuple[str, str, float]] = {}

    def issue(self, run_id: str, *, org_id: str) -> str:
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = (run_id, org_id, time.monotonic() + self._ttl)
        return ticket

    def consume(self, ticket: str) -> tuple[str, str] | None:
        entry = self._tickets.pop(ticket, None)
        if entry is None:
            return None
        run_id, org_id, expires_at = entry
        if time.monotonic() > expires_at:
            return None
        return run_id, org_id


def _tickets(request: Request) -> TicketStore:
    store = getattr(request.app.state, "tickets", None)
    if store is None:
        store = TicketStore()
        request.app.state.tickets = store
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

    return {"ticket": _tickets(request).issue(run_id, org_id=org_id)}


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
    claimed = _tickets(request).consume(ticket)
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
