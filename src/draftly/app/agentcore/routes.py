from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from draftly.workflows.state import WorkflowState

router = APIRouter(tags=["agentcore"])


class InvocationInput(BaseModel):
    event: dict[str, Any]


class InvocationRequest(BaseModel):
    input: InvocationInput


def _resolve_event_id(event: dict[str, Any], session_id: str) -> str:
    """Prefer an explicit event_id; else use the AgentCore session id (33+ chars)."""
    existing = event.get("event_id")
    if existing:
        return str(existing)
    if len(session_id) >= 33:
        return session_id
    return f"agentcore-{uuid4()}"


def _extract_trace_id(headers: Any) -> str | None:
    """Return the incoming W3C trace header (traceparent), if present."""
    value = headers.get("traceparent")
    if value:
        return str(value)
    return None


def _runner(request: Request) -> Any:
    draftly = getattr(request.app.state, "draftly", None)
    workflows = getattr(draftly, "workflows", None)
    runner = getattr(workflows, "runner", None)
    if runner is None:
        raise HTTPException(status_code=503, detail="workflow runner unavailable")
    return runner


@router.get("/ping")
async def ping() -> dict[str, str]:
    """AgentCore-required liveness endpoint (GET /ping)."""
    return {"status": "healthy"}


@router.post("/invocations")
async def invocations(
    invocation: InvocationRequest,
    request: Request,
) -> dict[str, Any]:
    """Run one normalized workflow event and return its terminal state."""
    event = dict(invocation.input.event)
    event_type = str(event.get("event_type") or "")
    if not event_type:
        raise HTTPException(
            status_code=400,
            detail="input.event.event_type is required",
        )

    session_id = request.headers.get("x-agentcore-session-id") or ""
    if not event.get("event_id"):
        event["event_id"] = _resolve_event_id(event, session_id)

    runner = _runner(request)

    try:
        state: WorkflowState = await runner.run(event)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"agent processing failed: {exc}",
        ) from exc

    output = state.to_dict()
    trace_id = _extract_trace_id(request.headers)
    if trace_id is not None:
        output["trace_id"] = trace_id
    return {"output": output}
