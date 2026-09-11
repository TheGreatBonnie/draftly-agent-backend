"""Public, safe response contracts for the agents dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    role: str
    name: str
    description: str
    surface: str
    tools: list[str] = Field(default_factory=list)
    availability: str = "enabled"
    last_run_status: str = "idle"
    runs_7d: int = 0
    success_rate_7d: float | None = None
    last_run_at: datetime | str | None = None
    latest_run_id: str | None = None
    legacy_steps: int = 0
    # Deprecated aliases retained for older dashboard clients.
    status: str | None = None
    activity: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


class AgentMetrics(BaseModel):
    window_days: int = 30
    runs: int = 0
    success_rate: float | None = None


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    source: str = ""
    event_type: str = "unknown"
    org_id: str = ""
    status: str = "unknown"
    error: str | None = None
    surface: str = ""
    workflow_key: str | None = None
    definition_id: str | None = None
    started_at: datetime | str | None = None
    completed_at: datetime | str | None = None


class RunStepSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    seq: int
    kind: str
    name: str
    status: str
    duration_ms: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    agent_id: str | None = None
    node_id: str | None = None
    surface: str = ""


class AgentListResponse(BaseModel):
    agents: list[AgentSummary]


class AgentDetailResponse(BaseModel):
    agent: AgentSummary
    metrics: AgentMetrics
    tools: list[str] = Field(default_factory=list)
    recent_runs: list[RunSummary] = Field(default_factory=list)


class AgentRunsResponse(BaseModel):
    items: list[RunSummary]
    next_cursor: str | None = None


class RunListResponse(BaseModel):
    items: list[RunSummary]


class RunStepsResponse(BaseModel):
    items: list[RunStepSummary]
