# app/api/routes/documentation.py

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

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
