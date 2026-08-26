import fakeredis
import pytest


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_record_and_get_stats(fake_redis):
    from draftly.models.redis_performance import RedisEMAStatsStore

    store = RedisEMAStatsStore(fake_redis, alpha=0.05)
    store.record_outcome("support", "gpt-4.1", success=True, latency_ms=150.0)
    stats = store.get_stats("support", "gpt-4.1")
    assert stats is not None
    assert stats.sample_count == 1
    assert stats.mean_latency_ms == 150.0


@pytest.mark.asyncio
async def test_ema_convergence(fake_redis):
    from draftly.models.redis_performance import RedisEMAStatsStore

    store = RedisEMAStatsStore(fake_redis, alpha=0.05)
    for _ in range(30):
        store.record_outcome("fast", "gpt-4.1-mini", success=True, latency_ms=100.0)
    stats = store.get_stats("fast", "gpt-4.1-mini")
    assert stats is not None
    assert stats.mean_latency_ms == pytest.approx(100.0, abs=1.0)


@pytest.mark.asyncio
async def test_has_enough_samples(fake_redis):
    from draftly.models.redis_performance import RedisEMAStatsStore

    store = RedisEMAStatsStore(fake_redis)
    assert store.has_enough_samples("x", "y") is False
    for _ in range(20):
        store.record_outcome("x", "y", success=True, latency_ms=10.0)
    assert store.has_enough_samples("x", "y") is True


@pytest.mark.asyncio
async def test_persistence_survives_restart(fake_redis):
    from draftly.models.redis_performance import RedisEMAStatsStore

    store = RedisEMAStatsStore(fake_redis)
    store.record_outcome("a", "b", success=True, latency_ms=200.0)
    # Simulate restart by creating new store pointing to same Redis
    store2 = RedisEMAStatsStore(fake_redis)
    stats = store2.get_stats("a", "b")
    assert stats is not None
    assert stats.sample_count == 1
