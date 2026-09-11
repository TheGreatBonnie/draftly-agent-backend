# app/api/routes/documentation.py

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from draftly.app.api.auth import get_verified_token
from draftly.evaluation.documentation_target import evaluate_document_content
from draftly.persistence.repositories.document_revisions import (
    DocumentationRevision,
    RevisionConflict,
)

router = APIRouter(
    prefix="/documentation",
    tags=["documentation"],
    dependencies=[Depends(get_verified_token)],
)


def derive_status(db_status: str | None) -> str:
    """Map DB status to the UI status enum (indexed -> published)."""
    mapping = {
        "indexed": "published",
        "published": "published",
        "needs-review": "needs-review",
        "needs-verification": "needs-verification",
        "stale": "stale",
    }
    return mapping.get(db_status or "", db_status or "published")


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


class RevisionRequest(BaseModel):
    content: str
    title: str | None = None
    base_source_hash: str | None = None
    base_revision_id: str | None = None


class EvaluationRequest(BaseModel):
    revision_id: str | None = None


def _worker(request: Request):
    """Resolve the background worker from application state."""
    worker = getattr(request.app.state.draftly, "worker", None)
    if worker is None:
        raise HTTPException(status_code=503, detail="Background worker is disabled")
    return worker


def _revisions(request: Request) -> Any:
    repo = getattr(
        getattr(request.app.state.draftly.dependencies, "repositories", None),
        "revisions",
        None,
    )
    if repo is None:
        raise HTTPException(status_code=503, detail="Revision store unavailable")
    return repo


def _revision_payload(revision: DocumentationRevision) -> dict[str, Any]:
    return {
        "id": revision.id,
        "document_id": revision.document_id,
        "org_id": revision.org_id,
        "revision_number": revision.revision_number,
        "origin": revision.origin,
        "status": revision.status,
        "title": revision.title,
        "content": revision.content,
        "base_source_hash": revision.base_source_hash,
        "created_by": revision.created_by,
        "created_at": revision.created_at.isoformat(),
    }


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
    scoped_get = jobs.get_for_org if hasattr(type(jobs), "get_for_org") else None
    record = (
        await scoped_get(job_id=job_id, org_id=token.get("org_id"))
        if callable(scoped_get)
        else await jobs.get(job_id=job_id)
    )
    if (
        record is not None
        and record.get("org_id") is not None
        and record.get("org_id") != token.get("org_id")
    ):
        record = None
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
    repository: str | None = None,
    status: str | None = None,
    query: str | None = None,
    limit: int = 1000,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """List documentation for the token org, optionally filtered by repo/status."""
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    docs = _documents(request)
    projection_method = (
        docs.list_projection_by_org if hasattr(type(docs), "list_projection_by_org") else None
    )
    if callable(projection_method):
        items = await projection_method(
            org_id=org_id,
            repository=repository,
            status=status,
            query=query,
            limit=min(limit, 1000),
            cursor=None,
        )
    else:
        items = await docs.list_by_org(org_id=org_id, limit=min(limit, 1000))
        if repository:
            items = [i for i in items if i.get("repository") == repository]
        if query:
            needle = query.lower()
            items = [
                i for i in items
                if needle in str(i.get("title") or "").lower()
                or needle in str(i.get("path") or "").lower()
            ]
        if status:
            items = [i for i in items if derive_status(i.get("status")) == status]
    return {"items": items, "total": len(items)}


@router.get("/stats")
async def documentation_stats(
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Real aggregates for the documentation filter cards."""
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    docs = _documents(request)
    items = await docs.list_by_org(org_id=org_id, limit=1000)
    by_status = Counter(derive_status(i.get("status")) for i in items)
    return {
        "total": len(items),
        "by_status": dict(by_status),
        "stale": sum(1 for i in items if i.get("stale")),
        "outdated": sum(1 for i in items if i.get("outdated")),
        "incomplete": sum(1 for i in items if i.get("incomplete")),
        "broken_links": sum(1 for i in items if i.get("broken_links")),
        "unsupported_claims": sum(1 for i in items if i.get("unsupported_claims")),
    }


@router.get("/{document_id}")
async def get_documentation(
    document_id: str,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Fetch one document by id, scoped to the token org."""
    org_id = token.get("org_id")
    docs = _documents(request)
    scoped_get = docs.get_for_org if hasattr(type(docs), "get_for_org") else None
    document = (
        await scoped_get(document_id=document_id, org_id=org_id)
        if callable(scoped_get)
        else await docs.get(document_id=document_id)
    )
    if document is None or (org_id and document.get("org_id") != org_id):
        raise HTTPException(
            status_code=404,
            detail=f"Document {document_id} not found",
        )
    revision_repo = getattr(
        getattr(request.app.state.draftly.dependencies, "repositories", None),
        "revisions",
        None,
    )
    draft = None
    draft_id = document.get("draft_revision_id")
    if revision_repo is not None and draft_id:
        draft = await revision_repo.get_revision(
            document_id=document_id,
            revision_id=str(draft_id),
            org_id=str(org_id),
        )
    result = dict(document)
    result["source"] = {
        "content": document.get("content", ""),
        "source_hash": document.get("source_hash"),
        "commit_sha": document.get("commit_sha"),
    }
    result["draft"] = _revision_payload(draft) if draft else None
    result["effective"] = (
        {"kind": "draft", "revision_id": draft.id, "content": draft.content}
        if draft
        else {
            "kind": "source",
            "revision_id": None,
            "content": document.get("content", ""),
        }
    )
    return result


@router.get("/{document_id}/revisions")
@router.get("/{document_id}/history")
async def list_document_revisions(
    document_id: str,
    request: Request,
    token: dict = Depends(get_verified_token),
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    page = await _revisions(request).list_revisions(
        document_id=document_id,
        org_id=str(token.get("org_id") or ""),
        limit=max(1, min(limit, 100)),
        cursor=cursor,
    )
    return {
        "items": [_revision_payload(item) for item in page.items],
        "total": page.total,
        "next_cursor": page.next_cursor,
    }


@router.post("/{document_id}/revisions")
async def create_document_revision(
    document_id: str,
    body: RevisionRequest,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    try:
        revision = await _revisions(request).create_draft(
            document_id=document_id,
            org_id=str(token.get("org_id") or ""),
            content=body.content,
            title=body.title,
            base_source_hash=body.base_source_hash,
            base_revision_id=body.base_revision_id,
            created_by=str(token.get("user_id") or token.get("sub") or "unknown"),
        )
    except RevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DOCUMENT_CONFLICT",
                "message": str(exc),
                "current_source_hash": exc.current_source_hash,
                "current_revision_id": exc.current_revision_id,
            },
        ) from exc
    return {"revision": _revision_payload(revision)}


@router.post("/{document_id}/revisions/{revision_id}/restore")
async def restore_document_revision(
    document_id: str,
    revision_id: str,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    try:
        revision = await _revisions(request).restore_revision(
            document_id=document_id,
            revision_id=revision_id,
            org_id=str(token.get("org_id") or ""),
            created_by=str(token.get("user_id") or token.get("sub") or "unknown"),
        )
    except RevisionConflict as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"revision": _revision_payload(revision)}


@router.get("/{document_id}/evaluations")
async def list_document_evaluations(
    document_id: str,
    request: Request,
    token: dict = Depends(get_verified_token),
    limit: int = 20,
) -> dict[str, Any]:
    document = await _documents(request).get_for_org(
        document_id=document_id,
        org_id=str(token.get("org_id") or ""),
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    evaluations = getattr(request.app.state.draftly.dependencies.repositories, "evaluations", None)
    if evaluations is None:
        raise HTTPException(status_code=503, detail="Evaluation store unavailable")
    items = await evaluations.search(
        org_id=str(token.get("org_id") or ""),
        evaluation_type="documentation",
        target_id=document_id,
        limit=max(1, min(limit, 100)),
    )
    return {"items": items, "total": len(items)}


@router.post("/{document_id}/evaluations")
async def evaluate_document(
    document_id: str,
    request: Request,
    body: EvaluationRequest | None = None,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = str(token.get("org_id") or "")
    document = await _documents(request).get_for_org(
        document_id=document_id,
        org_id=org_id,
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    content = str(document.get("content") or "")
    revision_id = body.revision_id if body else document.get("draft_revision_id")
    if revision_id:
        draft = await _revisions(request).get_revision(
            document_id=document_id,
            revision_id=str(revision_id),
            org_id=org_id,
        )
        if draft is None:
            raise HTTPException(status_code=404, detail="Revision not found")
        content = draft.content

    result = evaluate_document_content(content)
    evaluations = getattr(request.app.state.draftly.dependencies.repositories, "evaluations", None)
    if evaluations is None:
        raise HTTPException(status_code=503, detail="Evaluation store unavailable")
    run_id = str(uuid4())
    started_at = datetime.now(UTC)
    record = await evaluations.create(
        org_id=org_id,
        evaluation_type="documentation",
        run_id=run_id,
        target_type="documentation",
        target_id=document_id,
        score=result["score"],
        passed=result["passed"],
        status=result["status"],
        metrics={**result["metrics"], "revision_id": revision_id},
        failures=result["failures"],
        trace_id=run_id,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )
    return {"evaluation": record, "result": result}
