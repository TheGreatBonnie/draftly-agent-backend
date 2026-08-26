import pytest
import time
from fakeredis import aioredis


@pytest.fixture
def fake_redis():
    return aioredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_allows_within_limit(fake_redis):
    from draftly.integrations.rate_limiter import RateLimiter

    limiter = RateLimiter(fake_redis)
    for _ in range(5):
        assert await limiter.check("test:user1", limit=10, window_seconds=60) is True


@pytest.mark.asyncio
async def test_blocks_over_limit(fake_redis):
    from draftly.integrations.rate_limiter import RateLimiter

    limiter = RateLimiter(fake_redis)
    for _ in range(10):
        await limiter.check("test:user2", limit=10, window_seconds=60)
    assert await limiter.check("test:user2", limit=10, window_seconds=60) is False


@pytest.mark.asyncio
async def test_separate_keys_independent(fake_redis):
    from draftly.integrations.rate_limiter import RateLimiter

    limiter = RateLimiter(fake_redis)
    for _ in range(10):
        await limiter.check("test:a", limit=10, window_seconds=60)
    assert await limiter.check("test:b", limit=10, window_seconds=60) is True
