"""Redis-backed provider health with auto-expiring cooldowns."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

PREFIX = "draftly:health:"


class RedisProviderHealth:
    """Per-provider cooldown after failures, backed by Redis TTL."""

    def __init__(self, client: Any, cooldown_seconds: float = 300.0) -> None:
        self._client = client
        self._cooldown = cooldown_seconds

    def mark_failure(self, provider: str) -> None:
        key = f"{PREFIX}{provider}"
        self._client.set(key, "failed", ex=int(self._cooldown))

    def is_healthy(self, provider: str) -> bool:
        key = f"{PREFIX}{provider}"
        return self._client.exists(key) == 0

    def clear_failure(self, provider: str) -> None:
        key = f"{PREFIX}{provider}"
        self._client.delete(key)
