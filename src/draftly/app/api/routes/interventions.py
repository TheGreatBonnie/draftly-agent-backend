"""Authorized steering intervention response API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from draftly.app.api.auth import require_workflow_editor
from draftly.app.api.routes.workflow_runs import _repositories
from draftly.app.api.steering_schemas import InterventionResponse, InterventionResponseRequest
from draftly.persistence.repositories.steering import (
    InterventionConflictError,
    InterventionNotFoundError,
    InterventionRecord,
)
from draftly.steering.redaction import scrub_secret_values
from draftly.workflows.runner import InterventionResumeError

router = APIRouter(
    prefix="/workflow-runs/{run_id}/interventions/{interrupt_id}",
    tags=["interventions"],
)


def _runner(request: Request) -> Any:
    workflows = getattr(request.app.state.draftly, "workflows", None)
    return getattr(workflows, "runner", None) if workflows is not None else None


def _respond(
    record: InterventionRecord, run_status: str | None
) -> InterventionResponse:
    """Shape the safe response; never echoes the underlying reason or args."""
    return InterventionResponse(
        intervention_id=record.id,
        interrupt_id=record.interrupt_id,
        status=record.status,
        resolver=record.resolver_id,
        response_message=scrub_secret_values(record.response_message or "")
        if record.response_message
        else None,
        run_status=run_status,
    )


def _event_for(run: dict[str, Any], *, run_id: str, org_id: str) -> dict[str, Any]:
    return {
        "event_id": run_id,
        "event_type": str(run.get("event_type") or ""),
        "project_id": org_id,
        "workflow_key": str(run.get("workflow_key") or ""),
    }


def _run_status(run_state: Any, run: dict[str, Any] | None) -> str:
    state_status = getattr(run_state, "status", None)
    value = getattr(state_status, "value", state_status)
    if value:
        return str(value)
    return str((run or {}).get("status") or "")


@router.post("/respond", status_code=status.HTTP_200_OK)
async def respond_to_intervention(
    run_id: str,
    interrupt_id: str,
    payload: InterventionResponseRequest,
    request: Request,
    token: dict[str, Any] = Depends(require_workflow_editor),
) -> InterventionResponse:
    """Resolve one intervention under the caller's organization scope.

    The intervention's atomic claim and the one-shot graph resume live inside
    ``WorkflowRunner.resume_intervention``. An identical replay of the same
    idempotency key returns the already-known outcome without resuming the
    graph a second time; a conflicting replay returns 409; a missing, expired,
    or otherwise-resolved row returns 404.
    """
    repositories = _repositories(request)
    runs = getattr(repositories, "workflow_runs", None)
    interventions = getattr(repositories, "steering_interventions", None)
    runner = _runner(request)
    if runs is None or interventions is None or runner is None:
        raise HTTPException(status_code=503, detail="Intervention responses unavailable")

    org_id = str(token.get("org_id") or "")
    run = await runs.get(org_id=org_id, run_id=run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    action = payload.action.value
    message = payload.message
    idempotency_key = payload.idempotency_key
    pending = await interventions.get_pending(
        run_id=run_id, interrupt_id=interrupt_id, org_id=org_id
    )
    if pending is None:
        # Already resolved: replay the existing outcome if this call is the
        # original responder, without touching the graph.
        try:
            resolved = await interventions.claim_response(
                run_id=run_id,
                interrupt_id=interrupt_id,
                org_id=org_id,
                idempotency_key=idempotency_key,
                action=action,
                message=message,
            )
        except InterventionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except InterventionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _respond(resolved, run_status=_run_status(None, run))

    try:
        run_state = await runner.resume_intervention(
            event=_event_for(run, run_id=run_id, org_id=org_id),
            interrupt_id=interrupt_id,
            response={
                "action": action,
                "message": message,
                "idempotency_key": idempotency_key,
            },
        )
    except InterventionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InterventionResumeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # The claim already happened inside resume_intervention; re-read the
    # resolved row idempotently to echo the persisted outcome.
    try:
        resolved = await interventions.claim_response(
            run_id=run_id,
            interrupt_id=interrupt_id,
            org_id=org_id,
            idempotency_key=idempotency_key,
            action=action,
            message=message,
        )
    except (InterventionConflictError, InterventionNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _respond(resolved, run_status=_run_status(run_state, run))
