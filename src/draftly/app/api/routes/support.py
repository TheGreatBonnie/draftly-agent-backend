# app/api/routes/support.py

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from draftly.app.api.auth import get_verified_token

router = APIRouter(
    prefix="/support",
    tags=["support"],
    dependencies=[Depends(get_verified_token)],
)


def _support(request: Request) -> Any:
    application = request.app.state.draftly
    repo = getattr(
        getattr(application.dependencies, "repositories", None),
        "support",
        None,
    )
    if repo is None:
        raise HTTPException(status_code=503, detail="Store unavailable")
    return repo


@router.get("/questions")
async def list_support_questions(
    request: Request,
    platform: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List recent support questions (plan §9.1)."""
    repo = _support(request)
    messages = await repo.search_messages(
        "%",
        platform=platform,
        limit=max(1, min(limit, 200)),
    )
    return {
        "items": [
            {
                "id": str(m.id),
                "platform": m.platform,
                "channel_id": m.channel_id,
                "thread_id": m.thread_id,
                "author": m.author_name,
                "content": m.content,
                "timestamp": m.timestamp.isoformat()
                if getattr(m.timestamp, "isoformat", None)
                else m.timestamp,
            }
            for m in messages
        ]
    }


@router.get("/questions/{question_id}")
async def get_support_question(
    question_id: str,
    request: Request,
) -> dict[str, Any]:
    """Fetch one support thread by id."""
    repo = _support(request)
    thread = await repo.get_thread(question_id)
    if thread is None:
        raise HTTPException(
            status_code=404,
            detail=f"Support thread {question_id} not found",
        )
    return thread.model_dump(mode="json")
