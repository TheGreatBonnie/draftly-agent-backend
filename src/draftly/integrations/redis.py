"""Shared Redis connection pool. All Redis subsystems import from here."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class RedisClient:
    """Shared async Redis connection with health check."""

    def __init__(self, url: str = "redis://localhost:6379/0") -> None:
        import redis.asyncio as aioredis

        self._client = aioredis.from_url(url, decode_responses=True, socket_timeout=20)

    @property
    def native(self) -> Any:
        """Raw redis.asyncio.Redis for subsystems that need it."""
        return self._client

    async def health_check(self) -> bool:
        """Ping Redis; returns False on any connection error."""
        try:
            return await self._client.ping()
        except Exception as exc:
            logger.warning("redis_health_check_failed error=%s", exc)
            return False

    def pipeline(self) -> Any:
        """Create a pipeline for batched commands."""
        return self._client.pipeline(transaction=False)

    def get_rq_connection(self) -> Any:
        """Create a synchronous redis.Redis for RQ workers."""
        import redis as sync_redis

        kwargs = self._client.connection_pool.connection_kwargs
        return sync_redis.Redis(
            host=kwargs.get("host", "localhost"),
            port=kwargs.get("port", 6379),
            db=kwargs.get("db", 0),
            decode_responses=True,
        )

    async def close(self) -> None:
        """Gracefully close the connection pool."""
        try:
            await self._client.aclose()
        except Exception:
            pass
