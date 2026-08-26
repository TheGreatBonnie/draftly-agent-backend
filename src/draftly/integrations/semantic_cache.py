"""LLM response semantic cache backed by Redis."""

from __future__ import annotations

import hashlib
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

EXACT_PREFIX = "draftly:llmcache:exact:"


def _exact_key(prompt: str, model_id: str) -> str:
    content = f"{model_id}:{prompt}"
    return f"{EXACT_PREFIX}{hashlib.sha256(content.encode()).hexdigest()}"


class SemanticCache:
    """Cache LLM responses by prompt similarity.

    Phase 1: exact-match cache via SHA-256 hash.
    Phase 2 (future): add vector similarity via RediSearch.
    """

    def __init__(self, client: Any, similarity_threshold: float = 0.90) -> None:
        self._client = client
        self._threshold = similarity_threshold

    async def get(self, prompt: str, model_id: str) -> str | None:
        """Look up cached response for an exact prompt match."""
        try:
            key = _exact_key(prompt, model_id)
            return await self._client.get(key)
        except Exception:
            logger.warning("semantic_cache_get_failed", exc_info=True)
            return None

    async def set(
        self,
        prompt: str,
        response: str,
        model_id: str,
        ttl: int = 600,
    ) -> None:
        """Store a prompt-response pair."""
        try:
            key = _exact_key(prompt, model_id)
            await self._client.set(key, response, ex=ttl)
        except Exception:
            logger.warning("semantic_cache_set_failed", exc_info=True)

    async def invalidate(self, prompt: str, model_id: str) -> None:
        """Remove a cached entry."""
        try:
            key = _exact_key(prompt, model_id)
            await self._client.delete(key)
        except Exception:
            pass
