"""Review queue API (spec §Observability surface #1).

Read-only here: approve/reject keeps flowing through the existing
resume route POST /github/review/{run_id} so resume logic stays single-sourced.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from draftly.app.api.auth import get_verified_token
from draftly.persistence.repositories.reviews import ReviewRecord

router = APIRouter(
    prefix="/reviews", tags=["reviews"], dependencies=[Depends(get_verified_token)]
)


def review_to_dict(record: ReviewRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "org_id": record.org_id,
        "run_id": record.thread_id,
        "workflow": record.workflow,
        "tool_name": record.tool_name,
        "tool_args": record.tool_args,
        "action_description": record.action_description,
        "status": record.status,
        "decision": record.decision,
        "decision_comment": record.decision_comment,
        "decided_at": _iso(record.decided_at),
        "created_at": _iso(record.created_at),
        "expires_at": _iso(record.expires_at),
        "interrupt_id": (record.tool_args or {}).get("interrupt_id"),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _repo(request: Request) -> Any:
    repo = getattr(
        getattr(request.app.state.draftly.dependencies, "repositories", None),
        "reviews",
        None,
    )
    if repo is None:
        raise HTTPException(status_code=503, detail="Store unavailable")
    return repo


@router.get("")
async def list_reviews(
    request: Request,
    token: dict = Depends(get_verified_token),
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List reviews (default: all statuses) for the caller's organization."""
    items = await _repo(request).list_reviews(
        status=status,
        org_id=str(token.get("org_id") or ""),
        limit=max(1, min(limit, 200)),
    )
    return {"items": [review_to_dict(r) for r in items]}


@router.get("/{review_id}")
async def get_review(
    review_id: str,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    record = await _repo(request).get_review(review_id)
    if record is None or record.org_id != str(token.get("org_id")):
        raise HTTPException(status_code=404, detail=f"Unknown review: {review_id}")
    return {"review": review_to_dict(record)}
