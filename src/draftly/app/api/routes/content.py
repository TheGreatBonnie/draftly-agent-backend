"""Draft-only content production API."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from draftly.app.api.auth import get_verified_token
from draftly.content.models import ContentChannel, ContentPackageStatus, ContentRequest
from draftly.workflows.content.content_generation import run_content_generation

router = APIRouter(
    prefix="/content",
    tags=["content"],
    dependencies=[Depends(get_verified_token)],
)


class GenerateContentRequest(BaseModel):
    repository_id: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    source_event_type: Literal[
        "pull_request", "release", "documentation", "manual_brief", "feedback_gap"
    ]
    source_title: str = Field(min_length=1)
    source_summary: str = Field(min_length=1)
    source_feedback_ids: list[str] = Field(default_factory=list)
    source_gap_id: str | None = None
    source_evidence: list[dict[str, Any]] = Field(min_length=1)
    requested_channels: list[ContentChannel] = Field(min_length=1)
    audience: str = Field(min_length=1)
    tone: str = Field(min_length=1)


class ReviewContentRequest(BaseModel):
    decision: Literal["approve", "request_changes", "reject"]
    comment: str | None = None


def _repositories(request: Request) -> Any:
    repositories = getattr(request.app.state.draftly.dependencies, "repositories", None)
    content = getattr(repositories, "content", None) if repositories else None
    if content is None:
        raise HTTPException(status_code=503, detail="Content store unavailable")
    return content


def _org_id(token: dict[str, Any]) -> str:
    value = token.get("org_id")
    if not value:
        raise HTTPException(status_code=403, detail="Organization context required")
    return str(value)


@router.post("/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_content(
    body: GenerateContentRequest,
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = _org_id(token)
    content = _repositories(request)
    content_request = ContentRequest(org_id=org_id, **body.model_dump())
    run_id = f"content-{uuid4()}"
    application = getattr(request.app.state, "draftly", None)
    runner = getattr(getattr(application, "workflows", None), "runner", None)
    if runner is not None:
        event = body.model_dump(mode="json")
        event.update({
            "event_id": run_id,
            "event_type": "content.manual",
            "project_id": org_id,
            "org_id": org_id,
            "content_relevant": True,
        })
        state = await runner.run(event)
        package = await content.get_by_source(
            org_id=org_id,
            repository_id=body.repository_id,
            source_event_type=body.source_event_type,
            source_event_id=body.source_event_id,
        )
        if package is None:
            raise HTTPException(
                status_code=500,
                detail=state.errors or ["content package not persisted"],
            )
        package_id = package.id
        package_status = package.status.value
    else:
        state = await run_content_generation(content_request, repository=content, run_id=run_id)
        if state.status.value == "failed":
            raise HTTPException(status_code=500, detail=state.errors)
        package_id = state.result["package_id"]
        package_status = state.result["status"]
    return {
        "job_id": run_id,
        "package_id": package_id,
        "status": package_status,
    }


@router.get("")
async def list_content(
    request: Request,
    status: str | None = None,
    token: dict[str, Any] = Depends(get_verified_token),
) -> dict[str, Any]:
    packages = await _repositories(request).list(org_id=_org_id(token), status=status)
    return {"items": [package.model_dump(mode="json") for package in packages]}


@router.get("/{package_id}")
async def get_content(
    package_id: str,
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> dict[str, Any]:
    package = await _repositories(request).get(org_id=_org_id(token), package_id=package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="Content package not found")
    return package.model_dump(mode="json")


@router.post("/{package_id}/review")
async def review_content(
    package_id: str,
    body: ReviewContentRequest,
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = _org_id(token)
    content = _repositories(request)
    package = await content.get(org_id=org_id, package_id=package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="Content package not found")
    if body.decision == "request_changes" and not body.comment:
        raise HTTPException(status_code=400, detail="request_changes requires comment")
    if body.decision == "approve":
        if any(variant.evaluation.get("blocking_issues") for variant in package.variants):
            raise HTTPException(status_code=409, detail="Content has blocking evaluation issues")
        next_status = ContentPackageStatus.APPROVED
    elif body.decision == "reject":
        next_status = ContentPackageStatus.REJECTED
    else:
        from draftly.content.service import ContentService

        await ContentService(content).create_revision(
            package_id=package_id,
            revision_number=(package.revisions[-1].revision_number if package.revisions else 0) + 1,
            reason="request_changes",
            run_id=f"review-{uuid4()}",
            reviewer_comment=body.comment,
        )
        next_status = ContentPackageStatus.DRAFT
    await content.append_review_event(
        {
            "package_id": package_id,
            "revision_id": package.revisions[-1].id if package.revisions else None,
            "decision": body.decision,
            "comment": body.comment,
            "reviewer_id": str(token.get("sub") or token.get("user_id") or "reviewer"),
        }
    )
    await content.update_status(org_id=org_id, package_id=package_id, status=next_status)
    return {"package_id": package_id, "status": next_status.value, "comment": body.comment}


@router.get("/{package_id}/revisions")
async def list_revisions(
    package_id: str,
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> dict[str, Any]:
    package = await _repositories(request).get(org_id=_org_id(token), package_id=package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="Content package not found")
    return {"items": [revision.model_dump(mode="json") for revision in package.revisions]}
