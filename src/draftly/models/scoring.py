"""Spec-dimension weighted scoring with static-priority tie-breaker."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from draftly.models.config import ModelConfig
    from draftly.models.performance import EMAStatsStore
    from draftly.models.profiles import RoutingProfile
    from draftly.models.schemas import RoutingRequest

#: Samples required before recorded quality enters scoring (spec).
MIN_QUALITY_SAMPLES = 20

#: USD-per-request anchor for normalizing cost scores; matches the
#: unpriced sentinel so unknown-cost models land near the bottom.
COST_SCORE_ANCHOR = 0.50

#: p95 latency (ms) that maps to a zero latency score.
LATENCY_SCORE_CEILING_MS = 30_000.0


def score_candidates(
    request: RoutingRequest,
    candidates: list[ModelConfig],
    profile: RoutingProfile,
    stats_store: EMAStatsStore,
) -> list[tuple[ModelConfig, float]]:
    """Return candidates sorted by weighted score desc, then priority asc."""
    if not candidates:
        return []

    scored = [(_score_single(request, m, profile, stats_store), m) for m in candidates]
    # Static priority is ONLY the final tie-breaker (reference §26):
    # ascending ModelConfig.priority, never the request's priority.
    scored.sort(key=lambda pair: (-pair[0], pair[1].priority))
    return [(model, round(score, 6)) for score, model in scored]


def _score_single(
    request: RoutingRequest,
    model: ModelConfig,
    profile: RoutingProfile,
    store: EMAStatsStore,
) -> float:
    task = request.task_type.value
    total = (
        profile.w_quality * _quality_score(request, model, profile, store)
        + profile.w_reliability * _reliability_score(task, model, store)
        + profile.w_latency * _latency_score(task, model, store)
        + profile.w_cost * _cost_score(request, model)
        + profile.w_history * _history_score(task, model, store)
    )
    return total


def _quality_score(
    request: RoutingRequest,
    model: ModelConfig,
    profile: RoutingProfile,
    store: EMAStatsStore,
) -> float:
    """Task-scoped quality; conservative default (= floor) until samples >= 20."""
    task = request.task_type.value
    quality = store.get_quality(task, model.name)
    if quality is not None and store.sample_count(task, model.name) >= MIN_QUALITY_SAMPLES:
        return quality
    return profile.quality_floor


def _reliability_score(
    task: str, model: ModelConfig, store: EMAStatsStore
) -> float:
    rate = store.get_success_rate(task, model.name)
    return 0.95 if rate is None else rate


def _latency_score(task: str, model: ModelConfig, store: EMAStatsStore) -> float:
    p95 = store.get_latency_p95(task, model.name)
    if p95 is None:
        return 0.5
    return max(0.0, min(1.0, 1.0 - (p95 / LATENCY_SCORE_CEILING_MS)))


def _cost_score(request: RoutingRequest, model: ModelConfig) -> float:
    from draftly.models.constraints import estimate_request_cost

    cost = estimate_request_cost(model, request) or 0.0
    return max(0.0, min(1.0, 1.0 - (cost / COST_SCORE_ANCHOR)))


def _history_score(task: str, model: ModelConfig, store: EMAStatsStore) -> float:
    """Confidence that grows with observed volume for THIS task type.

    Measures volume, not a second copy of quality: it breaks ties toward
    battle-tested models without double-counting the same signal.
    DeepEval-scored quality flows through `_quality_score` once outcome
    recording lands (Task 16); `approval_rate` joins via the reviews hook
    noted there.
    """
    count = store.sample_count(task, model.name)
    return min(1.0, count / 100.0)
