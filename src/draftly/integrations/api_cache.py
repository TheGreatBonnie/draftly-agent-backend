"""FastAPI middleware for caching GET API responses in Redis."""

from __future__ import annotations

import hashlib
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

PREFIX = "draftly:apicache:"

CACHEABLE_PATHS: dict[str, int] = {
    "/api/workflows/": 10,
    "/api/repositories": 60,
    "/api/documentation": 30,
    "/api/observability/": 20,
}


class APICacheMiddleware:
    """Cache GET API responses in Redis."""

    def __init__(self, client: Any, ttl_seconds: int = 30) -> None:
        self._client = client
        self._default_ttl = ttl_seconds

    @staticmethod
    def cache_key(org_id: str, path: str) -> str:
        path_hash = hashlib.sha256(path.encode()).hexdigest()[:16]
        return f"{PREFIX}{org_id}:{path_hash}"

    async def get_cached(self, key: str) -> str | None:
        try:
            return await self._client.get(key)
        except Exception:
            return None

    async def set_cached(self, key: str, value: str, ttl: int | None = None) -> None:
        try:
            await self._client.set(key, value, ex=ttl or self._default_ttl)
        except Exception:
            pass

    async def invalidate_prefix(self, org_id: str, path_prefix: str) -> int:
        """Invalidate all cached responses matching a path prefix for an org."""
        pattern = f"{PREFIX}{org_id}:*"
        count = 0
        try:
            cursor = 0
            while True:
                cursor, keys = await self._client.scan(cursor, match=pattern, count=100)
                if keys:
                    await self._client.delete(*keys)
                    count += len(keys)
                if cursor == 0:
                    break
        except Exception:
            pass
        return count
