"""Sliding window rate limiter backed by Redis Sorted Sets."""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

PREFIX = "draftly:ratelimit:"


class RateLimiter:
    """Sliding window rate limiter using Redis Sorted Sets."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def check(self, key: str, limit: int, window_seconds: int) -> bool:
        """Return True if request is allowed, False if rate limited."""
        redis_key = f"{PREFIX}{key}"
        now = time.time()
        window_start = now - window_seconds
        member = f"{now}:{uuid.uuid4().hex[:8]}"

        pipe = self._client.pipeline(transaction=False)
        pipe.zremrangebyscore(redis_key, 0, window_start)
        pipe.zadd(redis_key, {member: now})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, window_seconds)
        results = await pipe.execute()

        count = results[2]
        return count <= limit
