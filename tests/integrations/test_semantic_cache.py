import pytest
from fakeredis import aioredis


@pytest.fixture
def fake_redis():
    return aioredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_exact_cache_hit(fake_redis):
    from draftly.integrations.semantic_cache import SemanticCache

    cache = SemanticCache(fake_redis, similarity_threshold=0.90)
    await cache.set(
        prompt="Classify this PR event",
        response='{"type": "documentation"}',
        model_id="gpt-4.1",
        ttl=600,
    )
    result = await cache.get(prompt="Classify this PR event", model_id="gpt-4.1")
    assert result == '{"type": "documentation"}'


@pytest.mark.asyncio
async def test_exact_cache_miss(fake_redis):
    from draftly.integrations.semantic_cache import SemanticCache

    cache = SemanticCache(fake_redis, similarity_threshold=0.90)
    result = await cache.get(prompt="Something different", model_id="gpt-4.1")
    assert result is None


@pytest.mark.asyncio
async def test_different_model_no_hit(fake_redis):
    from draftly.integrations.semantic_cache import SemanticCache

    cache = SemanticCache(fake_redis, similarity_threshold=0.90)
    await cache.set(
        prompt="Classify this",
        response="result",
        model_id="gpt-4.1",
        ttl=600,
    )
    result = await cache.get(prompt="Classify this", model_id="claude-3")
    assert result is None
