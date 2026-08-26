"""Redis-backed EMA statistics for model performance routing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

PREFIX = "draftly:ema:"


@dataclass
class TaskModelStats:
    """EMA aggregates for one (task_type, model_name) pair."""

    sample_count: int = 0
    mean_latency_ms: float = 0.0
    variance_latency_ms: float = 0.0
    success_rate: float = 1.0
    quality_ema: float | None = None
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0


class RedisEMAStatsStore:
    """Redis-backed live cache of per-task model performance.

    Keys are (task_type, model_name) pairs. Data persists across restarts.

    Note: p50/p95 percentile tracking is deferred (requires a bounded deque
    which is expensive in Redis). The Redis version tracks mean + variance
    only. The in-memory EMAStatsStore continues to track percentiles for
    routing decisions.
    """

    def __init__(self, client: Any, alpha: float = 0.05) -> None:
        self._client = client
        self._alpha = alpha

    def _key(self, task_type: str, model_name: str) -> str:
        return f"{PREFIX}{task_type}:{model_name}"

    def record_outcome(
        self,
        task_type: str,
        model_name: str,
        *,
        success: bool,
        latency_ms: float,
    ) -> None:
        """Record one invocation outcome synchronously, persist to Redis async."""
        key = self._key(task_type, model_name)
        raw = self._client.get(key)
        if raw is None:
            stats = TaskModelStats()
        else:
            data = json.loads(raw)
            stats = TaskModelStats(**data)

        a = self._alpha
        stats.sample_count += 1
        if stats.sample_count == 1:
            stats.mean_latency_ms = latency_ms
            stats.variance_latency_ms = 0.0
        else:
            delta = latency_ms - stats.mean_latency_ms
            stats.mean_latency_ms += a * delta
            stats.variance_latency_ms += a * (delta * delta - stats.variance_latency_ms)
        stats.success_rate = a * (1.0 if success else 0.0) + (1 - a) * stats.success_rate

        self._client.set(key, json.dumps({
            "sample_count": stats.sample_count,
            "mean_latency_ms": stats.mean_latency_ms,
            "variance_latency_ms": stats.variance_latency_ms,
            "success_rate": stats.success_rate,
            "quality_ema": stats.quality_ema,
            "p50_latency_ms": stats.p50_latency_ms,
            "p95_latency_ms": stats.p95_latency_ms,
        }))

    def record_quality(self, task_type: str, model_name: str, quality: float) -> None:
        key = self._key(task_type, model_name)
        raw = self._client.get(key)
        if raw is None:
            stats = TaskModelStats()
        else:
            data = json.loads(raw)
            stats = TaskModelStats(**data)

        stats.sample_count += 1
        if stats.quality_ema is None:
            stats.quality_ema = quality
        else:
            stats.quality_ema = self._alpha * quality + (1 - self._alpha) * stats.quality_ema

        self._client.set(key, json.dumps({
            "sample_count": stats.sample_count,
            "mean_latency_ms": stats.mean_latency_ms,
            "variance_latency_ms": stats.variance_latency_ms,
            "success_rate": stats.success_rate,
            "quality_ema": stats.quality_ema,
            "p50_latency_ms": stats.p50_latency_ms,
            "p95_latency_ms": stats.p95_latency_ms,
        }))

    def get_stats(self, task_type: str, model_name: str) -> TaskModelStats | None:
        raw = self._client.get(self._key(task_type, model_name))
        if raw is None:
            return None
        data = json.loads(raw)
        return TaskModelStats(**data)

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
