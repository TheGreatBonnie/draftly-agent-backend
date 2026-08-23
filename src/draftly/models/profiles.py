"""Routing profiles with quality floors and scoring weights.

Dimensions follow the spec table exactly: quality, reliability, latency,
cost, history (task-scoped historical performance).
"""

from __future__ import annotations

from dataclasses import dataclass

from draftly.models.schemas import TaskType


@dataclass(frozen=True)
class RoutingProfile:
    """Immutable profile defining weight emphasis and quality floor."""

    name: str
    w_quality: float
    w_reliability: float
    w_latency: float
    w_cost: float
    w_history: float
    quality_floor: float

    def __post_init__(self) -> None:
        total = (
            self.w_quality + self.w_reliability + self.w_latency
            + self.w_cost + self.w_history
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Weights must sum to 1.0, got {total}")
        if not 0.0 <= self.quality_floor <= 1.0:
            raise ValueError(f"quality_floor must be 0-1, got {self.quality_floor}")


ROUTING_PROFILES: dict[str, RoutingProfile] = {
    "support": RoutingProfile(
        name="support", w_quality=0.25, w_reliability=0.15, w_latency=0.35,
        w_cost=0.25, w_history=0.0, quality_floor=0.80,
    ),
    "fast": RoutingProfile(
        name="fast", w_quality=0.25, w_reliability=0.15, w_latency=0.35,
        w_cost=0.25, w_history=0.0, quality_floor=0.80,
    ),
    "reasoning": RoutingProfile(
        # Derived row (spec table covers 4 workloads; reasoning/research are
        # quality-leaning per reference §34): quality HIGH, latency low.
        name="reasoning", w_quality=0.40, w_reliability=0.25, w_latency=0.15,
        w_cost=0.10, w_history=0.10, quality_floor=0.88,
    ),
    "documentation_generation": RoutingProfile(
        name="documentation_generation", w_quality=0.40, w_reliability=0.25,
        w_latency=0.10, w_cost=0.10, w_history=0.15, quality_floor=0.90,
    ),
    "documentation_review": RoutingProfile(
        name="documentation_review", w_quality=0.50, w_reliability=0.30,
        w_latency=0.05, w_cost=0.10, w_history=0.05, quality_floor=0.93,
    ),
    "evaluation": RoutingProfile(
        name="evaluation", w_quality=0.50, w_reliability=0.30,
        w_latency=0.05, w_cost=0.10, w_history=0.05, quality_floor=0.93,
    ),
    "delivery": RoutingProfile(
        name="delivery", w_quality=0.40, w_reliability=0.35, w_latency=0.15,
        w_cost=0.10, w_history=0.0, quality_floor=0.95,
    ),
}

_TASK_TYPE_PROFILES: dict[TaskType, RoutingProfile] = {
    TaskType.SUPPORT: ROUTING_PROFILES["support"],
    TaskType.FAST: ROUTING_PROFILES["fast"],
    TaskType.REASONING: ROUTING_PROFILES["reasoning"],
    TaskType.RESEARCH: ROUTING_PROFILES["reasoning"],
    TaskType.DOCUMENTATION_GENERATION: ROUTING_PROFILES["documentation_generation"],
    TaskType.DOCUMENTATION_REVIEW: ROUTING_PROFILES["documentation_review"],
    TaskType.EVALUATION: ROUTING_PROFILES["evaluation"],
    TaskType.DELIVERY: ROUTING_PROFILES["delivery"],
}


def get_profile(
    task_type: TaskType,
    default: RoutingProfile | None = None,
) -> RoutingProfile:
    """Return the routing profile for a task type."""
    return _TASK_TYPE_PROFILES.get(task_type, default or ROUTING_PROFILES["support"])
