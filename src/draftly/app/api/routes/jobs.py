# app/api/routes/jobs.py

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from draftly.app.api.auth import get_verified_token
from draftly.app.composition.rq_jobs import enqueue_job
from draftly.integrations.database.jobs_store import DatabaseJobsStore

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(get_verified_token)],
)


class JobRequest(BaseModel):
    job_name: str
    arguments: dict[str, Any] = {}


@router.post("/run")
async def run_job(
    body: JobRequest,
    request: Request,
) -> dict[str, Any]:
    """
    Enqueue a registered Draftly job for background execution.
    """

    application = request.app.state.draftly

    rq_queues = getattr(application, "rq_queues", None)
    task_handlers = getattr(application, "task_handlers", None)

    if rq_queues is None or task_handlers is None:
        raise HTTPException(
            status_code=503,
            detail="RQ worker is not initialized",
        )

    if body.job_name not in task_handlers:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown job: {body.job_name}",
        )

    job = enqueue_job(
        queues=rq_queues,
        task_handlers=task_handlers,
        task_name=body.job_name,
        **body.arguments,
    )

    # Sync to Postgres jobs table for frontend polling
    store = DatabaseJobsStore()
    await store.insert(
        org_id="system",
        name=body.job_name,
        job_type=body.job_name,
        schedule="manual",
        configuration={"arguments": body.arguments, "rq_job_id": job.id},
    )

    return {
        "status": "queued",
        "job_id": job.id,
    }


@router.get("")
async def list_jobs() -> dict[str, Any]:
    """
    List active background jobs.
    """

    store = DatabaseJobsStore()
    rows = await store.list_active()

    return {
        "items": rows,
    }


@router.get("/{job_id}")
async def get_job(
    job_id: str,
) -> dict[str, Any]:
    """
    Get job detail by ID.
    """

    store = DatabaseJobsStore()
    row = await store.get(job_id=job_id)

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Job not found: {job_id}",
        )

    return row
