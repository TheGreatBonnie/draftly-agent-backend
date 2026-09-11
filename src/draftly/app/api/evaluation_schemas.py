"""Public, bounded response contracts for the evaluations dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

EvaluationStatus = Literal[
    "queued",
    "running",
    "passed",
    "failed",
    "cancelled",
    "skipped",
]
Score = float | None
T = TypeVar("T")


class CursorPage(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="ignore")

    items: list[T] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    next_cursor: str | None = None


class EvaluationRunSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    run_id: str
    name: str = "Evaluation run"
    evaluation_type: str
    datasets: list[str] = Field(default_factory=list)
    cases: int = Field(default=0, ge=0)
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    score: Score = Field(default=None, ge=0, le=100)
    status: EvaluationStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class EvaluationCaseResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    run_id: str
    evaluation_id: str
    dataset: str
    case_id: str
    metric: str
    threshold: Score = Field(default=None, ge=0, le=100)
    score: Score = Field(default=None, ge=0, le=100)
    passed: bool
    reason: str = ""
    input: str | None = None
    expected_output: str | None = None
    actual_output: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class EvaluationTrendPoint(BaseModel):
    date: str
    average_score: float = Field(ge=0, le=100)
    run_count: int = Field(default=0, ge=0)


class EvaluationMetricAggregate(BaseModel):
    metric: str
    average_score: float = Field(ge=0, le=100)
    sample_count: int = Field(default=0, ge=0)


class EvaluationAggregateSummary(BaseModel):
    window_days: Literal[1, 7, 14, 30]
    average_score: Score = Field(default=None, ge=0, le=100)
    total_runs: int = Field(default=0, ge=0)
    passed_runs: int = Field(default=0, ge=0)
    failed_runs: int = Field(default=0, ge=0)
    total_cases: int = Field(default=0, ge=0)
    pass_rate: float | None = Field(default=None, ge=0, le=1)
    trend: list[EvaluationTrendPoint] = Field(default_factory=list)
    by_metric: list[EvaluationMetricAggregate] = Field(default_factory=list)


class EvaluationDataset(BaseModel):
    name: str
    description: str = ""
    surface: str = ""
    case_count: int = Field(default=0, ge=0)
    version: str | None = None


class EvaluationEvaluator(BaseModel):
    key: str
    display_name: str
    description: str = ""
    threshold: Score = Field(default=None, ge=0, le=100)
    version: str | None = None
    enabled: bool = True


class EvaluationCatalog(BaseModel):
    datasets: list[EvaluationDataset] = Field(default_factory=list)
    evaluators: list[EvaluationEvaluator] = Field(default_factory=list)


class EvaluationRunDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: EvaluationRunSummary
    datasets: list[EvaluationDataset] = Field(default_factory=list)
    evaluators: list[EvaluationEvaluator] = Field(default_factory=list)
    cases: list[EvaluationCaseResult] = Field(default_factory=list)
    next_cases_cursor: str | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    detail_available: bool = True


class EvaluationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasets: list[str] | None = None
    live: bool = False
    profile: str | None = None
