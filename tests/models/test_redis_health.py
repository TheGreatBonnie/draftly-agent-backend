import pytest
import fakeredis


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


def test_healthy_by_default(fake_redis):
    from draftly.models.redis_health import RedisProviderHealth

    health = RedisProviderHealth(fake_redis, cooldown_seconds=300)
    assert health.is_healthy("bedrock") is True


def test_failure_marks_unhealthy(fake_redis):
    from draftly.models.redis_health import RedisProviderHealth

    health = RedisProviderHealth(fake_redis, cooldown_seconds=300)
    health.mark_failure("bedrock")
    assert health.is_healthy("bedrock") is False


def test_clear_failure(fake_redis):
    from draftly.models.redis_health import RedisProviderHealth

    health = RedisProviderHealth(fake_redis, cooldown_seconds=300)
    health.mark_failure("bedrock")
    health.clear_failure("bedrock")
    assert health.is_healthy("bedrock") is True
