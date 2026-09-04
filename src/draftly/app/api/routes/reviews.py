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
        "detail": record.detail or {},
        "pr": None,
    }


async def review_to_dict_enriched(record: ReviewRecord, request: Request) -> dict[str, Any]:
    """Enrich review dict with PR identity from github_workflows (best-effort)."""
    base = review_to_dict(record)
    try:
        deps = getattr(request.app.state.draftly, "dependencies", None)
        integrations = getattr(deps, "integrations", None) if deps else None
        db = getattr(integrations, "database", None) if integrations else None
        if db is None:
            deps2 = getattr(request.app.state.draftly, "dependencies", None)
            integ2 = getattr(deps2, "integrations", None) if deps2 else None
            db = getattr(integ2, "database", None) if integ2 else None
        from draftly.persistence.repositories.github import get_github_workflow_by_run_id

        gw = await get_github_workflow_by_run_id(run_id=record.thread_id, db=db)
        if gw:
            label = f"PR #{gw.get('issue_number')}" if gw.get("issue_number") else None
            base["pr"] = {
                "title": gw.get("title"),
                "trigger_label": label,
                "actor": gw.get("actor"),
                "owner": gw.get("owner"),
                "repo": gw.get("repo"),
                "issue_number": gw.get("issue_number"),
            }
    except Exception:
        # Best-effort — if no github_workflows row exists (e.g. non-PR), return pr: null
        pass
    return base


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
    enriched = [await review_to_dict_enriched(r, request) for r in items]
    return {"items": enriched}


@router.get("/by-run/{run_id}")
async def get_review_by_run(
    run_id: str,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    record = await _repo(request).get_pending_by_run_id(run_id)
    if record is None or record.org_id != str(token.get("org_id")):
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return {"review": await review_to_dict_enriched(record, request)}


@router.get("/{review_id}")
async def get_review(
    review_id: str,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    record = await _repo(request).get_review(review_id)
    if record is None or record.org_id != str(token.get("org_id")):
        raise HTTPException(status_code=404, detail=f"Unknown review: {review_id}")
    return {"review": await review_to_dict_enriched(record, request)}
