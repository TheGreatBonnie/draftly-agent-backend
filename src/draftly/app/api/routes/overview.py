"""Authenticated dashboard overview endpoint."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import AfterValidator, BaseModel

from draftly.app.api.auth import get_verified_token
from draftly.app.services.overview import build_overview_snapshot


class OverviewSummary(BaseModel):
    documentation_total: int
    active_workflows: int
    running_workflows: int
    scheduled_workflows: int
    pending_reviews: int
    average_evaluation_score: float | None


class OverviewAttention(BaseModel):
    pending_reviews: int
    high_risk_reviews: int
    failed_evaluations: int
    integration_issues: int
    stale_documentation: int


class OverviewSystem(BaseModel):
    agents_online: int
    agents_total: int
    data_sources_connected: int
    data_sources_total: int
    evaluations_status: Literal["Running", "Idle", "Failed", "Unknown"]
    scheduler_status: Literal["Healthy", "Idle", "Unavailable"]


class OverviewRecentChange(BaseModel):
    id: str
    title: str
    detail: str
    timestamp: str | None
    status: str
    href: str


class OverviewWorkflow(BaseModel):
    id: str
    name: str
    repository: str
    status: str
    timestamp: str | None
    href: str


class OverviewEvaluationDimension(BaseModel):
    name: str
    value: float


class OverviewEvaluation(BaseModel):
    average_score: float | None
    trend: float | None
    dimensions: list[OverviewEvaluationDimension]


class OverviewActivityPoint(BaseModel):
    date: str
    created: int
    updated: int
    reviewed: int
    published: int


class OverviewResponse(BaseModel):
    summary: OverviewSummary
    attention: OverviewAttention
    system: OverviewSystem
    recent_changes: list[OverviewRecentChange]
    active_workflows: list[OverviewWorkflow]
    evaluation: OverviewEvaluation
    activity: list[OverviewActivityPoint]


router = APIRouter(
    prefix="/overview",
    tags=["overview"],
    dependencies=[Depends(get_verified_token)],
)


def _validate_days(value: int) -> int:
    if value not in {1, 7, 14, 30}:
        raise ValueError("days must be one of 1, 7, 14, or 30")
    return value


OverviewDays = Annotated[int, AfterValidator(_validate_days)]


@router.get("", response_model=OverviewResponse)
async def get_overview(
    request: Request,
    days: OverviewDays = 14,
    token: dict[str, Any] = Depends(get_verified_token),
) -> OverviewResponse:
    """Return the overview for the organization carried by the Clerk token."""
    application = request.app.state.draftly
    return await build_overview_snapshot(application, str(token["org_id"]), days)
