import pytest
from fakeredis import aioredis


@pytest.fixture
def fake_redis():
    return aioredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_create_index(fake_redis):
    from draftly.integrations.redis_vector_search import RedisVectorSearch

    search = RedisVectorSearch(fake_redis)
    assert search._client is fake_redis


@pytest.mark.asyncio
async def test_store_and_search(fake_redis):
    from draftly.integrations.redis_vector_search import RedisVectorSearch

    search = RedisVectorSearch(fake_redis)
    embedding = [0.1] * 1536
    await search.store(
        org_id="org-123",
        memory_item_id="item-1",
        namespace="knowledge",
        embedding=embedding,
        importance=0.8,
    )
    key = "draftly:vec:org-123:item-1"
    assert await fake_redis.exists(key) == 1


@pytest.mark.asyncio
async def test_delete(fake_redis):
    from draftly.integrations.redis_vector_search import RedisVectorSearch

    search = RedisVectorSearch(fake_redis)
    await search.store(
        org_id="org-123",
        memory_item_id="item-2",
        namespace="knowledge",
        embedding=[0.1] * 1536,
        importance=0.5,
    )
    await search.delete(org_id="org-123", memory_item_id="item-2")
    key = "draftly:vec:org-123:item-2"
    assert await fake_redis.exists(key) == 0
