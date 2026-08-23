# src/draftly/models/performance.py
"""Task-scoped EMA statistics and model-level health cooldowns."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

#: Bounded latency window for percentile estimates.
_LATENCY_WINDOW = 250


@dataclass
class TaskModelStats:
    """EMA aggregates for one (task_type, model_name) pair."""

    sample_count: int = 0
    mean_latency_ms: float = 0.0
    variance_latency_ms: float = 0.0
    success_rate: float = 1.0         # EMA of outcomes (1=success, 0=failure)
    quality_ema: float | None = None  # EMA of evaluation/quality scores
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    _latencies: deque = field(default_factory=lambda: deque(maxlen=_LATENCY_WINDOW),
                              repr=False)


class EMAStatsStore:
    """In-memory live cache of per-task model performance.

    Keys are ``(task_type, model_name)`` pairs so routing learns that a
    model is good at documentation but mediocre at support (reference §13).
    """

    def __init__(self, alpha: float = 0.05) -> None:
        self._alpha = alpha
        self._stats: dict[tuple[str, str], TaskModelStats] = {}

    # -- recording -------------------------------------------------------

    def _entry(self, task_type: str, model_name: str) -> TaskModelStats:
        key = (task_type, model_name)
        if key not in self._stats:
            self._stats[key] = TaskModelStats()
        return self._stats[key]

    def record_outcome(
        self,
        task_type: str,
        model_name: str,
        *,
        success: bool,
        latency_ms: float,
    ) -> None:
        """Record one invocation outcome (latency + success flag)."""
        a = self._alpha
        stats = self._entry(task_type, model_name)
        stats.sample_count += 1
        if stats.sample_count == 1:
            stats.mean_latency_ms = latency_ms
            stats.variance_latency_ms = 0.0
        else:
            delta = latency_ms - stats.mean_latency_ms
            stats.mean_latency_ms += a * delta
            stats.variance_latency_ms += a * (delta * delta - stats.variance_latency_ms)
        stats.success_rate = a * (1.0 if success else 0.0) + (1 - a) * stats.success_rate
        stats._latencies.append(latency_ms)
        ordered = sorted(stats._latencies)
        n = len(ordered)
        stats.p50_latency_ms = ordered[n // 2]
        stats.p95_latency_ms = ordered[min(int(n * 0.95), n - 1)]

    def record_quality(self, task_type: str, model_name: str, quality: float) -> None:
        """Record an evaluation/quality observation as an EMA."""
        stats = self._entry(task_type, model_name)
        stats.sample_count += 1
        if stats.quality_ema is None:
            stats.quality_ema = quality
        else:
            stats.quality_ema = (
                self._alpha * quality + (1 - self._alpha) * stats.quality_ema
            )

    # -- reads ---------------------------------------------------------------

    def get_stats(self, task_type: str, model_name: str) -> TaskModelStats | None:
        return self._stats.get((task_type, model_name))

    def sample_count(self, task_type: str, model_name: str) -> int:
        stats = self.get_stats(task_type, model_name)
        return stats.sample_count if stats else 0

    def has_enough_samples(
        self, task_type: str, model_name: str, threshold: int = 20
    ) -> bool:
        return self.sample_count(task_type, model_name) >= threshold

    def get_quality(self, task_type: str, model_name: str) -> float | None:
        stats = self.get_stats(task_type, model_name)
        return stats.quality_ema if stats else None

    def get_success_rate(self, task_type: str, model_name: str) -> float | None:
        stats = self.get_stats(task_type, model_name)
        if stats and stats.sample_count > 0:
            return stats.success_rate
        return None

    def get_latency_p95(self, task_type: str, model_name: str) -> float | None:
        stats = self.get_stats(task_type, model_name)
        if stats and stats.p95_latency_ms > 0:
            return stats.p95_latency_ms
        return None


class ModelHealthRegistry:
    """Per-model cooldown after failures (complements ProviderHealthRegistry)."""

    def __init__(self, cooldown_seconds: float = 300.0) -> None:
        self._cooldown = cooldown_seconds
        self._failures: dict[str, float] = {}

    def mark_failure(self, model_name: str) -> None:
        self._failures[model_name] = time.time()

    def is_model_healthy(self, model_name: str) -> bool:
        if model_name not in self._failures:
            return True
        return (time.time() - self._failures[model_name]) >= self._cooldown

    def clear_failure(self, model_name: str) -> None:
        self._failures.pop(model_name, None)


def get_model_p95_latency(
    task_type: str,
    model_name: str,
    store: EMAStatsStore,
) -> float | None:
    return store.get_latency_p95(task_type, model_name)
