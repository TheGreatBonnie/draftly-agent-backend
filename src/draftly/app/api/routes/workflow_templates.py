"""Organization-scoped workflow template endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from draftly.app.api.auth import get_verified_token, require_workflow_editor
from draftly.app.api.workflow_schemas import (
    WorkflowDefinitionCreate,
    WorkflowTemplateCreate,
    WorkflowTemplateInstantiate,
)

router = APIRouter(prefix="/workflow-templates", tags=["workflow-templates"])


def _repositories(request: Request) -> Any:
    return request.app.state.draftly.dependencies.repositories


@router.get("")
async def list_templates(
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
    limit: int = 50,
) -> dict[str, Any]:
    repo = getattr(_repositories(request), "workflow_templates", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Workflow templates unavailable")
    return {"items": await repo.list(org_id=str(token.get("org_id") or ""), limit=limit)}


@router.get("/{template_id}")
async def get_template(
    template_id: str,
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> dict[str, Any]:
    repo = getattr(_repositories(request), "workflow_templates", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Workflow templates unavailable")
    row = await repo.get(org_id=str(token.get("org_id") or ""), template_id=template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template": row}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: WorkflowTemplateCreate,
    request: Request,
    token: dict[str, Any] = Depends(require_workflow_editor),
) -> dict[str, Any]:
    repo = getattr(_repositories(request), "workflow_templates", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Workflow templates unavailable")
    row = await repo.create(org_id=str(token.get("org_id") or ""), payload=payload)
    return {"template": row}


@router.post("/{template_id}/instantiate", status_code=status.HTTP_201_CREATED)
async def instantiate_template(
    template_id: str,
    payload: WorkflowTemplateInstantiate,
    request: Request,
    token: dict[str, Any] = Depends(require_workflow_editor),
) -> dict[str, Any]:
    repositories = _repositories(request)
    templates = getattr(repositories, "workflow_templates", None)
    definitions = getattr(repositories, "workflow_definitions", None)
    if templates is None or definitions is None:
        raise HTTPException(status_code=503, detail="Workflow resources unavailable")
    org_id = str(token.get("org_id") or "")
    template = await templates.get(org_id=org_id, template_id=template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    merged = {**dict(template.get("defaults") or {}), **payload.overrides}
    name = payload.name or merged.pop("name", template.get("name", "Workflow"))
    slug = payload.slug or merged.pop("slug", template.get("slug", "workflow"))
    description = (
        payload.description if payload.description is not None else merged.pop("description", None)
    )
    allowed_config = {
        "trigger_config",
        "condition_config",
        "agent_config",
        "repository_config",
        "evaluation_config",
        "review_config",
        "delivery_config",
    }
    definition_payload = WorkflowDefinitionCreate(
        name=name,
        slug=slug,
        description=description,
        workflow_key=template["workflow_key"],
        status="draft",
        **{key: value for key, value in merged.items() if key in allowed_config},
    )
    row = await definitions.create(
        org_id=org_id,
        created_by=str(token.get("user_id") or token.get("sub") or "") or None,
        payload=definition_payload,
    )
    return {"workflow": row, "template_id": template_id}
