# app/api/routes/documentation.py

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from draftly.app.api.auth import get_verified_token

router = APIRouter(
    prefix="/documentation",
    tags=["documentation"],
    dependencies=[Depends(get_verified_token)],
)


def _documents(request: Request) -> Any:
    application = request.app.state.draftly
    repo = getattr(
        getattr(application.dependencies, "repositories", None),
        "documents",
        None,
    )
    if repo is None:
        raise HTTPException(status_code=503, detail="Store unavailable")
    return repo


class SyncRequest(BaseModel):
    repository_full_name: str
    include: list[str] | None = None
    exclude: list[str] | None = None


def _worker(request: Request):
    """Resolve the background worker from application state."""
    worker = getattr(request.app.state.draftly, "worker", None)
    if worker is None:
        raise HTTPException(status_code=503, detail="Background worker is disabled")
    return worker


@router.post("/sync")
async def sync_documentation(
    request: Request,
    body: SyncRequest,
    token: dict = Depends(get_verified_token),
) -> JSONResponse:
    """Submit documentation sync for a repository (spec: streaming plan T7).

    Returns 202 immediately with the job id; the task runs in the
    background and its outcome lands on the job record, queryable via
    GET /sync/{job_id} (fallback polling) and observable live via the
    workflow SSE stream once events_streaming_enabled.
    """
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    worker = _worker(request)
    if not worker.task_runner.has_task("documentation.sync_repository"):
        raise HTTPException(status_code=404, detail="Unknown job: documentation.sync_repository")

    jobs = getattr(
        request.app.state.draftly.dependencies.repositories, "jobs", None
    )
    if jobs is None:
        raise HTTPException(status_code=503, detail="Jobs store unavailable")

    job_id = str(uuid4())

    await jobs.insert(
        run_id=job_id,
        org_id=org_id,
        name="documentation.sync_repository",
        job_type="documentation",
        schedule="manual",
        configuration={
            "repository": body.repository_full_name,
            "include": body.include,
            "exclude": body.exclude,
        },
    )

    async def _run_and_mark() -> None:
        try:
            result = await worker.run_task(
                "documentation.sync_repository",
                org_id=org_id,
                repository_full_name=body.repository_full_name,
                include=body.include,
                exclude=body.exclude,
            )
            await jobs.update_status(job_id=job_id, status="completed")
            del result
        except Exception:
            await jobs.update_status(job_id=job_id, status="failed")

    asyncio.create_task(_run_and_mark())

    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "run_id": job_id, "status": "submitted"},
    )


@router.get("/sync/{job_id}")
async def get_sync_status(
    job_id: str,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Look up a sync run's job record."""
    jobs = request.app.state.draftly.dependencies.repositories.jobs
    record = await jobs.get(job_id=job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return {"job": record}


@router.get("/baseline")
async def get_baseline(
    request: Request,
    repository: str,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Live baseline: current indexed state for a repository.

    v1 executes sync synchronously and returns the BaselineSnapshot in the
    POST /sync response; this endpoint reports the live indexed state.
    """
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    documents = _documents(request)
    rows = await documents.list_by_org(org_id=org_id, limit=1000)
    repo_rows = [row for row in rows if row.get("repository") == repository]
    shas = [row.get("commit_sha") for row in repo_rows if row.get("commit_sha")]
    latest = Counter(shas).most_common(1)[0][0] if shas else None

    return {
        "repository": repository,
        "document_count": len(repo_rows),
        "latest_commit_sha": latest,
        "stale_count": sum(1 for row in repo_rows if row.get("status") == "stale"),
    }


@router.get("")
async def list_documentation(
    request: Request,
    repository: str,
) -> dict[str, Any]:
    """List generated documentation for a repository (plan §9.1)."""
    repo = _documents(request)
    items = await repo.find_by_repository(repository=repository)
    return {"items": items}


@router.get("/{document_id}")
async def get_documentation(
    document_id: str,
    request: Request,
) -> dict[str, Any]:
    """Fetch one generated document by id."""
    repo = _documents(request)
    document = await repo.get(document_id=document_id)
    if document is None:
        raise HTTPException(
            status_code=404,
            detail=f"Document {document_id} not found",
        )
    return document
