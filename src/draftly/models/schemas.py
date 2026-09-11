"""Core routing schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskType(StrEnum):
    """Routing task types (spec §Routing profiles)."""

    SUPPORT = "support"
    FAST = "fast"
    REASONING = "reasoning"
    RESEARCH = "research"
    DOCUMENTATION_GENERATION = "documentation_generation"
    DOCUMENTATION_REVIEW = "documentation_review"
    EVALUATION = "evaluation"
    DELIVERY = "delivery"


#: Explicit mapping from real agent roles to task types. Agent role names
#: do NOT match TaskType values (only ``research`` coincides), so this map
#: — not string identity — is the single source of truth.
ROLE_TO_TASK_TYPE: dict[str, TaskType] = {
    "documentation_engineer": TaskType.DOCUMENTATION_GENERATION,
    "documentation_reviewer": TaskType.DOCUMENTATION_REVIEW,
    "github_intelligence": TaskType.RESEARCH,
    "support_engineer": TaskType.SUPPORT,
    "support_reviewer": TaskType.DOCUMENTATION_REVIEW,
    "research": TaskType.RESEARCH,
    "deepeval": TaskType.EVALUATION,
    "github_delivery": TaskType.DELIVERY,
    "memory_curator": TaskType.FAST,
    "classifier": TaskType.FAST,
    "notify": TaskType.FAST,
    "context": TaskType.RESEARCH,
    "knowledge_extractor": TaskType.DOCUMENTATION_GENERATION,
    "initial_evaluator": TaskType.EVALUATION,
    "recommender": TaskType.DOCUMENTATION_REVIEW,
    "content_strategist": TaskType.RESEARCH,
    "content_blog_writer": TaskType.DOCUMENTATION_GENERATION,
    "content_social_adapter": TaskType.DOCUMENTATION_GENERATION,
    "content_judge": TaskType.EVALUATION,
}


@dataclass(frozen=True)
class RoutingRequest:
    """Request to route a task to the best model."""

    task_type: TaskType
    context_tokens: int
    estimated_output_tokens: int = 1024
    priority: int = 5
    cost_budget: float | None = None
    latency_budget_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutingDecision:
    """Immutable record of a routing decision (spec §Decision)."""

    selected_model: str
    provider: str
    score: float
    candidates_considered: int
    profile: str
    task_type: str | None = None
    estimated_cost: float | None = None
    estimated_latency_ms: float | None = None
    rejected: dict[str, list[str]] | None = None
    ranked: tuple[tuple[str, float], ...] = ()
    reason_codes: tuple[str, ...] = ()
    fallback_chain: tuple[str, ...] = ()
