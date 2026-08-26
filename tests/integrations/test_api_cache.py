import pytest
from fakeredis import aioredis


@pytest.fixture
def fake_redis():
    return aioredis.FakeRedis(decode_responses=True)


def test_cache_key_generation():
    from draftly.integrations.api_cache import APICacheMiddleware

    key = APICacheMiddleware.cache_key("org-123", "/api/repositories")
    assert key.startswith("draftly:apicache:")
    assert "org-123" in key


@pytest.mark.asyncio
async def test_cache_set_and_get(fake_redis):
    from draftly.integrations.api_cache import APICacheMiddleware

    middleware = APICacheMiddleware(fake_redis, ttl_seconds=30)
    key = "draftly:apicache:test:endpoint"
    await middleware.set_cached(key, '{"data": "test"}')
    result = await middleware.get_cached(key)
    assert result == '{"data": "test"}'


@pytest.mark.asyncio
async def test_cache_miss_returns_none(fake_redis):
    from draftly.integrations.api_cache import APICacheMiddleware

    middleware = APICacheMiddleware(fake_redis, ttl_seconds=30)
    result = await middleware.get_cached("draftly:apicache:nonexistent")
    assert result is None
