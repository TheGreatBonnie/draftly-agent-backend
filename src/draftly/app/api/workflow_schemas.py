"""Validated public contracts for workflow definitions, templates, and runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DefinitionStatus = Literal["active", "paused", "draft", "archived"]
RunStatus = Literal[
    "queued",
    "running",
    "pending_review",
    "completed",
    "failed",
    "cancelled",
    "skipped",
]
WorkflowKey = Literal[
    "github_pr",
    "github_release",
    "github_issue",
    "github_feedback",
    "slack_support",
    "discord_support",
    "documentation_sync",
    "documentation_audit",
    "feedback_loop",
    "content_generation",
    "evaluation_loop",
    "onboarding_initialize",
    "memory_curation",
    "memory_maintenance",
    "stale_run_reconcile",
]

_SECRET_KEY_PARTS = ("token", "secret", "password", "api_key", "private_key")
_MAX_CONFIG_DEPTH = 8
_MAX_CONFIG_KEYS = 200


def _validate_safe_config(value: dict[str, Any] | None) -> dict[str, Any]:
    """Reject credential-shaped keys before config reaches storage."""
    if value is None:
        return {}

    key_count = 0

    def visit(node: Any, depth: int = 0) -> None:
        nonlocal key_count
        if depth > _MAX_CONFIG_DEPTH:
            raise ValueError("configuration nesting exceeds the supported depth")
        if isinstance(node, dict):
            key_count += len(node)
            if key_count > _MAX_CONFIG_KEYS:
                raise ValueError("configuration contains too many keys")
            for key, child in node.items():
                normalized = str(key).lower().replace("-", "_")
                if any(part in normalized for part in _SECRET_KEY_PARTS):
                    raise ValueError(f"sensitive configuration key is not allowed: {key}")
                visit(child, depth + 1)
        elif isinstance(node, list):
            for child in node:
                visit(child, depth + 1)

    visit(value)
    return value


class _WorkflowModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowDefinitionCreate(_WorkflowModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    workflow_key: WorkflowKey
    description: str | None = Field(default=None, max_length=2000)
    status: DefinitionStatus = "draft"
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    condition_config: dict[str, Any] = Field(default_factory=dict)
    agent_config: dict[str, Any] = Field(default_factory=dict)
    repository_config: dict[str, Any] = Field(default_factory=dict)
    evaluation_config: dict[str, Any] = Field(default_factory=dict)
    review_config: dict[str, Any] = Field(default_factory=dict)
    delivery_config: dict[str, Any] = Field(default_factory=dict)

    _safe_trigger = field_validator("trigger_config")(_validate_safe_config)
    _safe_condition = field_validator("condition_config")(_validate_safe_config)
    _safe_agent = field_validator("agent_config")(_validate_safe_config)
    _safe_repository = field_validator("repository_config")(_validate_safe_config)
    _safe_evaluation = field_validator("evaluation_config")(_validate_safe_config)
    _safe_review = field_validator("review_config")(_validate_safe_config)
    _safe_delivery = field_validator("delivery_config")(_validate_safe_config)


class WorkflowDefinitionPatch(_WorkflowModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    workflow_key: WorkflowKey | None = None
    status: DefinitionStatus | None = None
    trigger_config: dict[str, Any] | None = None
    condition_config: dict[str, Any] | None = None
    agent_config: dict[str, Any] | None = None
    repository_config: dict[str, Any] | None = None
    evaluation_config: dict[str, Any] | None = None
    review_config: dict[str, Any] | None = None
    delivery_config: dict[str, Any] | None = None

    _safe_trigger = field_validator("trigger_config")(_validate_safe_config)
    _safe_condition = field_validator("condition_config")(_validate_safe_config)
    _safe_agent = field_validator("agent_config")(_validate_safe_config)
    _safe_repository = field_validator("repository_config")(_validate_safe_config)
    _safe_evaluation = field_validator("evaluation_config")(_validate_safe_config)
    _safe_review = field_validator("review_config")(_validate_safe_config)
    _safe_delivery = field_validator("delivery_config")(_validate_safe_config)


class WorkflowDefinitionResponse(_WorkflowModel):
    id: str
    org_id: str
    name: str
    slug: str
    description: str | None
    workflow_key: WorkflowKey
    status: DefinitionStatus
    version: int
    trigger_config: dict[str, Any]
    condition_config: dict[str, Any]
    agent_config: dict[str, Any]
    repository_config: dict[str, Any]
    evaluation_config: dict[str, Any]
    review_config: dict[str, Any]
    delivery_config: dict[str, Any]
    created_by: str | None
    created_at: datetime | None
    updated_at: datetime | None


class WorkflowTemplateResponse(_WorkflowModel):
    id: str
    org_id: str | None
    slug: str
    name: str
    description: str | None
    workflow_key: WorkflowKey
    defaults: dict[str, Any]
    is_system: bool
    created_at: datetime | None
    updated_at: datetime | None


class WorkflowRunResponse(_WorkflowModel):
    id: str
    definition_id: str | None
    source: str = "manual"
    source_event_id: str | None = None
    event_type: str | None = None
    title: str | None
    repository: str | None
    actor: str | None
    target: dict[str, Any] | None = None
    status: RunStatus = "queued"
    current_stage: str | None
    stage_states: dict[str, Any]
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    error: str | None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowListResponse(_WorkflowModel):
    items: list[WorkflowDefinitionResponse]
    summary: dict[str, Any]
    total: int
    next_cursor: str | None = None


class WorkflowRunListResponse(_WorkflowModel):
    items: list[WorkflowRunResponse]
    total: int
    next_cursor: str | None = None


class WorkflowTemplateCreate(_WorkflowModel):
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    workflow_key: WorkflowKey
    defaults: dict[str, Any] = Field(default_factory=dict)

    _safe_defaults = field_validator("defaults")(_validate_safe_config)


class WorkflowTemplateInstantiate(_WorkflowModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    slug: str | None = Field(default=None, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str | None = Field(default=None, max_length=2000)
    overrides: dict[str, Any] = Field(default_factory=dict)

    _safe_overrides = field_validator("overrides")(_validate_safe_config)


class WorkflowRunCreate(_WorkflowModel):
    source: str = Field(default="manual", min_length=1, max_length=80)
    source_event_id: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=500)
    input: dict[str, Any] | None = None
    target: dict[str, Any] | None = None

    _safe_input = field_validator("input")(_validate_safe_config)
    _safe_target = field_validator("target")(_validate_safe_config)


class WorkflowManualRunCreate(WorkflowRunCreate):
    definition_id: str


__all__ = [
    "DefinitionStatus",
    "RunStatus",
    "WorkflowDefinitionCreate",
    "WorkflowDefinitionPatch",
    "WorkflowDefinitionResponse",
    "WorkflowKey",
    "WorkflowListResponse",
    "WorkflowRunCreate",
    "WorkflowManualRunCreate",
    "WorkflowRunListResponse",
    "WorkflowRunResponse",
    "WorkflowTemplateCreate",
    "WorkflowTemplateInstantiate",
    "WorkflowTemplateResponse",
]
