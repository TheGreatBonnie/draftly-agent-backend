# app/api/routes/jobs.py

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from draftly.app.api.auth import get_verified_token

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
    Manually execute a registered Draftly job.
    """

    application = request.app.state.draftly

    worker = application.worker

    if worker is None:
        raise HTTPException(
            status_code=503,
            detail="Background worker is disabled",
        )

    if not worker.task_runner.has_task(
        body.job_name,
    ):
        raise HTTPException(
            status_code=404,
            detail=f"Unknown job: {body.job_name}",
        )

    result = await worker.run_task(
        body.job_name,
        **body.arguments,
    )

    return {
        "status": "completed",
        "result": result,
    }
