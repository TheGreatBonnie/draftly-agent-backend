from unittest.mock import AsyncMock, MagicMock

import pytest


def test_redis_client_exposes_native():
    from draftly.integrations.redis import RedisClient

    mock_redis = MagicMock()
    client = RedisClient.__new__(RedisClient)
    client._client = mock_redis
    assert client.native is mock_redis


def test_redis_client_default_url():
    from draftly.integrations.redis import RedisClient

    client = RedisClient(url="redis://localhost:6379/0")
    assert client._client is not None


@pytest.mark.asyncio
async def test_health_check_success():
    from draftly.integrations.redis import RedisClient

    client = RedisClient.__new__(RedisClient)
    client._client = AsyncMock()
    client._client.ping = AsyncMock(return_value=True)
    assert await client.health_check() is True


@pytest.mark.asyncio
async def test_health_check_failure_returns_false():
    from draftly.integrations.redis import RedisClient

    client = RedisClient.__new__(RedisClient)
    client._client = AsyncMock()
    client._client.ping = AsyncMock(side_effect=ConnectionError("refused"))
    assert await client.health_check() is False
