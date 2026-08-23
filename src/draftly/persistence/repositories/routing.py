"""Repository layer for routing decisions and model performance."""

from __future__ import annotations

from typing import Any

from draftly.models.performance import EMAStatsStore, TaskModelStats
from draftly.persistence.stores.routing import DatabasePerformanceStore, DatabaseRoutingStore


class RoutingRepository:
    def __init__(self, store: DatabaseRoutingStore) -> None:
        self._store = store

    async def record(self, decision_row: dict[str, Any]) -> None:
        await self._store.record_decision(decision_row)

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._store.get_recent_decisions(limit)


class PerformanceRepository:
    """Durability + warm-start bridge for the live EMA cache."""

    def __init__(self, store: DatabasePerformanceStore) -> None:
        self._store = store
        self._stats_store: EMAStatsStore | None = None

    def bind_stats_store(self, stats_store: EMAStatsStore) -> None:
        """Share the router's live cache (called once at composition)."""
        self._stats_store = stats_store

    async def record_outcome(
        self,
        *,
        task_type: str,
        model_name: str,
        success: bool,
        latency_ms: float,
    ) -> None:
        # 1. Live cache first: the very next route() call sees it.
        if self._stats_store is not None:
            self._stats_store.record_outcome(
                task_type, model_name, success=success, latency_ms=latency_ms
            )
        # 2. Durable aggregate.
        await self.flush_entry(task_type, model_name)

    async def flush_entry(self, task_type: str, model_name: str) -> None:
        if self._stats_store is None:
            return
        stats = self._stats_store.get_stats(task_type, model_name)
        if stats is None:
            return
        await self._store.upsert_performance({
            "model_name": model_name,
            "task_type": task_type,
            "sample_count": stats.sample_count,
            "mean_latency_ms": stats.mean_latency_ms,
            "variance_latency_ms": stats.variance_latency_ms,
            "success_rate": stats.success_rate,
            "p50_latency_ms": stats.p50_latency_ms,
            "p95_latency_ms": stats.p95_latency_ms,
            "quality_ema": stats.quality_ema,
            "approval_rate": None,
        })

    async def record_quality(
        self, *, task_type: str, model_name: str, quality: float
    ) -> None:
        if self._stats_store is not None:
            self._stats_store.record_quality(task_type, model_name, quality)
        await self.flush_entry(task_type, model_name)

    async def warm_start(self) -> None:
        """Load persisted aggregates into the live cache at boot."""
        if self._stats_store is None:
            return
        for row in await self._store.get_all():
            stats = TaskModelStats()
            stats.sample_count = row["sample_count"]
            stats.mean_latency_ms = row["mean_latency_ms"] or 0.0
            stats.variance_latency_ms = row["variance_latency_ms"] or 0.0
            stats.success_rate = row["success_rate"] if row["success_rate"] is not None else 1.0
            stats.p50_latency_ms = row["p50_latency_ms"] or 0.0
            stats.p95_latency_ms = row["p95_latency_ms"] or 0.0
            stats.quality_ema = row["quality_ema"]
            key = (row["task_type"], row["model_name"])
            self._stats_store._stats[key] = stats  # direct seed; EMA resumes from history
