"""Review queue API (spec §Observability surface #1).

Read-only here: approve/reject keeps flowing through the existing
resume route POST /github/review/{run_id} so resume logic stays single-sourced.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from draftly.app.api.auth import get_verified_token
from draftly.persistence.repositories.reviews import ReviewRecord, review_counts_from_records

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


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value) * 100 if value <= 1 else float(value)
    return max(0.0, min(100.0, normalized))


def _display_file(file: Any) -> dict[str, Any] | None:
    if not isinstance(file, dict):
        return None
    original = file.get("original_content")
    proposed = file.get("proposed_content", file.get("content"))
    available = file.get("original_content_available")
    if not isinstance(available, bool):
        available = isinstance(original, str)
    return {
        "path": str(file.get("path") or file.get("name") or ""),
        "action": file.get("action"),
        "original_content": original if isinstance(original, str) else None,
        "proposed_content": proposed if isinstance(proposed, str) else None,
        "original_content_available": available,
    }


def _display_files(document: dict[str, Any]) -> list[dict[str, Any]]:
    files = document.get("files")
    if isinstance(files, list):
        return [item for file in files if (item := _display_file(file)) is not None]

    content = document.get("content", document.get("body"))
    if isinstance(content, str):
        item = _display_file(
            {
                "path": document.get("path") or document.get("title") or "Generated document",
                "action": "create",
                "proposed_content": content,
                "original_content_available": False,
            }
        )
        return [item] if item else []
    return []


def build_review_display(record: ReviewRecord, raw: dict[str, Any]) -> dict[str, Any]:
    """Build the nullable-safe read model consumed by review pages."""
    detail = _dict(record.detail)
    document = _dict(detail.get("document"))
    pr = _dict(raw.get("pr"))
    classification = _dict(detail.get("classification"))
    evaluation = _dict(detail.get("evaluation"))
    files = _display_files(document)
    dimensions = evaluation.get("dimensions")
    if not isinstance(dimensions, list):
        dimensions = []
    reasons = evaluation.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    count = evaluation.get("count")
    if not isinstance(count, int) or isinstance(count, bool):
        count = None
    repository = _text(
        document.get("repository"),
        "/".join(str(part) for part in (pr.get("owner"), pr.get("repo")) if part),
    )
    title = _text(
        document.get("title"),
        pr.get("title"),
        document.get("summary"),
        detail.get("summary"),
        record.action_description,
        files[0].get("path") if files else None,
    )
    issue_number = pr.get("issue_number")
    github_url = _text(pr.get("url"), pr.get("html_url"), pr.get("github_url"))
    if github_url is None and pr.get("owner") and pr.get("repo") and issue_number:
        github_url = f"https://github.com/{pr['owner']}/{pr['repo']}/pull/{issue_number}"
    updated_at = _iso(record.decided_at) or _iso(record.created_at)

    return {
        "title": title,
        "reference": _text(pr.get("trigger_label"), document.get("reference")),
        "description": _text(
            document.get("description"),
            document.get("summary"),
            detail.get("summary"),
            record.action_description,
        ),
        "repository": repository,
        "files": files,
        "change_type": _text(classification.get("change_type"), document.get("change_type")),
        "risk": _text(
            classification.get("risk"),
            classification.get("risk_level"),
            classification.get("urgency"),
            document.get("risk"),
        ),
        "evaluation": {
            "overall_score": _score(
                evaluation.get("overall_score", evaluation.get("score"))
            ),
            "dimensions": dimensions,
            "reasons": [str(reason) for reason in reasons],
            "count": count,
        },
        "evidence": detail.get("evidence") if isinstance(detail.get("evidence"), list) else [],
        "github_url": github_url,
        "updated_at": updated_at,
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
    base["display"] = build_review_display(record, base)
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
    cursor: str | None = None,
) -> dict[str, Any]:
    """List reviews (default: all statuses) for the caller's organization."""
    repo = _repo(request)
    org_id = str(token.get("org_id") or "")
    page = getattr(repo, "list_reviews_page", None)
    if page is not None:
        result = await page(
            status=status,
            org_id=org_id,
            limit=max(1, min(limit, 200)),
            cursor=cursor,
        )
        items = result["items"]
        total = result["total"]
        counts = result["counts"]
        next_cursor = result.get("next_cursor")
    else:
        items = await repo.list_reviews(
            status=status,
            org_id=org_id,
            limit=max(1, min(limit, 200)),
        )
        total = len(items)
        counts = review_counts_from_records(items)
        next_cursor = None
    enriched = [await review_to_dict_enriched(r, request) for r in items]
    return {
        "items": enriched,
        "total": total,
        "counts": counts,
        "next_cursor": next_cursor,
    }


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
