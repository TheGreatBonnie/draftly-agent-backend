"""Ordered hard-constraint filtering with soft-constraint relaxation.

Precedence (reference §33): provider enabled -> capabilities ->
context window -> provider health -> model health -> quality floor
(known data only) -> [soft] cost -> [soft] latency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from draftly.models.config import ModelConfig
    from draftly.models.health import ProviderHealthRegistry
    from draftly.models.performance import EMAStatsStore, ModelHealthRegistry
    from draftly.models.schemas import RoutingRequest

#: Requests at or below this size may use models whose context_window is
#: undeclared; larger requests require a declared window.
_UNKNOWN_WINDOW_FLOOR = 16_000


@dataclass
class ConstraintPipeline:
    """Filters candidates through ordered hard constraints, then relaxes soft ones."""

    _rejected: dict[str, list[str]] = field(default_factory=dict, init=False, repr=False)

    @property
    def rejected(self) -> dict[str, list[str]]:
        return dict(self._rejected)

    def filter_candidates(
        self,
        request: RoutingRequest,
        candidates: list[ModelConfig],
        *,
        provider_health: ProviderHealthRegistry,
        model_health: ModelHealthRegistry,
        enabled_providers: set[str],
        required_caps: set[str] | None = None,
        stats_store: EMAStatsStore | None = None,
    ) -> list[ModelConfig]:
        self._rejected = {"hard": [], "soft": []}
        remaining = list(candidates)

        remaining = self._filter_by_provider(remaining, enabled_providers)
        if required_caps:
            remaining = self._filter_by_capabilities(remaining, required_caps)
        remaining = self._filter_by_context_window(remaining, request.context_tokens)
        remaining = self._filter_by_provider_health(remaining, provider_health)
        remaining = self._filter_by_model_health(remaining, model_health)
        if stats_store is not None and remaining:
            remaining = self._filter_by_quality_floor(
                remaining, request.task_type.value, stats_store
            )

        if not remaining:
            return []

        # Soft 7: cost budget (relaxable) — estimated with the request's tokens
        if request.cost_budget is not None:
            soft = [
                m for m in remaining
                if (estimate_request_cost(m, request) or 0.0) <= request.cost_budget
            ]
            if soft:
                remaining = soft
            else:
                self._rejected["soft"].append("cost_budget")

        # Soft 8: latency budget (relaxable)
        if request.latency_budget_ms is not None and stats_store is not None:
            soft = [
                m for m in remaining
                if (stats_store.get_latency_p95(request.task_type.value, m.name) or 0.0)
                <= request.latency_budget_ms
            ]
            if soft:
                remaining = soft
            else:
                self._rejected["soft"].append("latency_budget")

        return remaining

    def _filter_by_provider(
        self, candidates: list[ModelConfig], enabled: set[str]
    ) -> list[ModelConfig]:
        result = [m for m in candidates if m.provider in enabled]
        self._rejected["hard"].extend(
            f"provider_disabled:{m.name}" for m in candidates if m.provider not in enabled
        )
        return result

    def _filter_by_capabilities(
        self, candidates: list[ModelConfig], required: set[str]
    ) -> list[ModelConfig]:
        result = [m for m in candidates if required.issubset(set(m.capabilities))]
        self._rejected["hard"].extend(
            f"missing_capability:{m.name}"
            for m in candidates if not required.issubset(set(m.capabilities))
        )
        return result

    def _filter_by_context_window(
        self, candidates: list[ModelConfig], tokens: int
    ) -> list[ModelConfig]:
        kept: list[ModelConfig] = []
        rejected: list[ModelConfig] = []
        for m in candidates:
            if m.context_window is None:
                (kept if tokens <= _UNKNOWN_WINDOW_FLOOR else rejected).append(m)
            elif m.context_window >= tokens:
                kept.append(m)
            else:
                rejected.append(m)
        self._rejected["hard"].extend(f"context_window_exceeded:{m.name}" for m in rejected)
        return kept

    def _filter_by_provider_health(
        self, candidates: list[ModelConfig], registry: ProviderHealthRegistry
    ) -> list[ModelConfig]:
        result = [m for m in candidates if registry.get(m.provider).available()]
        self._rejected["hard"].extend(
            f"provider_unhealthy:{m.name}" for m in candidates
            if not registry.get(m.provider).available()
        )
        return result

    def _filter_by_model_health(
        self, candidates: list[ModelConfig], registry: ModelHealthRegistry
    ) -> list[ModelConfig]:
        result = [m for m in candidates if registry.is_model_healthy(m.name)]
        self._rejected["hard"].extend(
            f"model_in_cooldown:{m.name}" for m in candidates
            if not registry.is_model_healthy(m.name)
        )
        return result

    def _filter_by_quality_floor(
        self, candidates: list[ModelConfig], task_type: str, store: EMAStatsStore
    ) -> list[ModelConfig]:
        """Eliminate candidates whose KNOWN quality is below the profile floor.

        Unknown quality never eliminates (spec: gate on samples >= 20);
        enforcement for unknown models happens via conservative defaults
        in scoring instead.
        """
        from draftly.models.profiles import get_profile
        from draftly.models.schemas import TaskType
        from draftly.models.scoring import MIN_QUALITY_SAMPLES

        floor = get_profile(TaskType(task_type)).quality_floor
        kept: list[ModelConfig] = []
        rejected: list[ModelConfig] = []
        for m in candidates:
            quality = store.get_quality(task_type, m.name)
            if (
                quality is not None
                and store.sample_count(task_type, m.name) >= MIN_QUALITY_SAMPLES
                and quality < floor
            ):
                rejected.append(m)
            else:
                kept.append(m)
        self._rejected["hard"].extend(f"below_quality_floor:{m.name}" for m in rejected)
        return kept


def estimate_request_cost(model: ModelConfig, request: RoutingRequest) -> float | None:
    """Cost estimate using the request's token estimates."""
    from draftly.models.pricing import estimate_cost

    return estimate_cost(
        model,
        input_tokens=request.context_tokens,
        output_tokens=request.estimated_output_tokens,
    )
