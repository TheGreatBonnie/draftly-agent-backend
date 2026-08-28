# tests/unit/models/test_performance.py
"""Tests for task-scoped EMA stats and model health registry."""

import pytest

from draftly.models.performance import (
    EMAStatsStore,
    ModelHealthRegistry,
    get_model_p95_latency,
)


def test_ema_initializes_on_first_sample():
    store = EMAStatsStore(alpha=0.05)
    store.record_outcome("support", "model_a", success=True, latency_ms=100.0)
    stats = store.get_stats("support", "model_a")
    assert stats is not None
    assert stats.sample_count == 1
    assert stats.mean_latency_ms == 100.0


def test_ema_updates_incrementally():
    store = EMAStatsStore(alpha=0.05)
    store.record_outcome("support", "m", success=True, latency_ms=100.0)
    store.record_outcome("support", "m", success=True, latency_ms=200.0)
    stats = store.get_stats("support", "m")
    assert stats is not None
    assert stats.sample_count == 2
    assert 100.0 < stats.mean_latency_ms < 200.0


def test_task_scoping_isolates_models_and_tasks():
    store = EMAStatsStore()
    store.record_outcome("support", "m", success=True, latency_ms=100.0)
    store.record_outcome("delivery", "m", success=False, latency_ms=900.0)
    assert store.get_stats("support", "m") is not None
    assert store.get_stats("delivery", "m") is not None
    assert store.get_stats("review", "m") is None


def test_quality_is_ema_not_plain_average():
    store = EMAStatsStore(alpha=0.05)
    for _ in range(20):
        store.record_quality("support", "m", 0.9)
    store.record_quality("support", "m", 0.5)  # outlier must move EMA only slightly
    q = store.get_quality("support", "m")
    assert q is not None
    assert q > 0.85  # a plain average would have dropped much lower
    assert q < 0.9


def test_sample_gate_counts_all_outcomes():
    store = EMAStatsStore()
    for _ in range(19):
        store.record_outcome("support", "m", success=True, latency_ms=100.0)
    assert not store.has_enough_samples("support", "m")
    store.record_quality("support", "m", 0.9)
    assert store.sample_count("support", "m") >= 20
    assert store.has_enough_samples("support", "m")


def test_success_rate_reflects_failures():
    store = EMAStatsStore()
    for _ in range(10):
        store.record_outcome("support", "m", success=True, latency_ms=100.0)
    store.record_outcome("support", "m", success=False, latency_ms=100.0)
    rate = store.get_success_rate("support", "m")
    assert rate is not None and rate < 1.0


def test_percentiles_from_bounded_history():
    store = EMAStatsStore()
    for i in range(300):  # exceeds any internal window; must not blow up
        store.record_outcome("support", "m", success=True, latency_ms=float(i))
    p95 = store.get_latency_p95("support", "m")
    assert p95 is not None and p95 > 0


def test_model_health_cooldown():
    registry = ModelHealthRegistry(cooldown_seconds=60)
    registry.mark_failure("m")
    assert not registry.is_model_healthy("m")
    registry.clear_failure("m")
    assert registry.is_model_healthy("m")


def test_get_model_p95_latency_requires_task_scope():
    store = EMAStatsStore()
    assert get_model_p95_latency("support", "unknown", store) is None


class _FakePerformanceStore:
    """In-memory store capturing upserts for the repo<->store bridge."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def upsert_performance(self, row: dict) -> None:
        self.rows.append(row)

    async def get_all(self) -> list[dict]:
        return list(self.rows)


@pytest.mark.asyncio
async def test_record_outcome_flush_false_defers_upsert():
    from draftly.persistence.repositories.routing import PerformanceRepository

    store = _FakePerformanceStore()
    repo = PerformanceRepository(store=store)
    repo.bind_stats_store(EMAStatsStore())

    # Default flush=True preserves the legacy per-call per-record upsert.
    await repo.record_outcome(
        task_type="fast", model_name="m", success=True, latency_ms=12.0,
    )
    assert len(store.rows) == 1

    # flush=False → live EMA cache only; no durable upsert yet.
    await repo.record_outcome(
        task_type="fast", model_name="m", success=True, latency_ms=20.0,
        flush=False,
    )
    assert len(store.rows) == 1

    await repo.flush_entry("fast", "m")
    assert len(store.rows) == 2
    assert store.rows[1]["sample_count"] == 2
    assert store.rows[1]["success_rate"] == 1.0
