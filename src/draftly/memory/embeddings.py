"""Embedding generation for memory (plan §8.1).

Uses the Phase 2 ``EmbeddingRouter`` when a provider is configured;
falls back to a deterministic hashing embedder so memory writes never
fail in offline/CI environments (no model keys).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

FALLBACK_DIMENSIONS = 256


def _hash_embed(text: str, dimensions: int = FALLBACK_DIMENSIONS) -> list[float]:
    """Deterministic token-hash embedding (offline fallback)."""
    vector = [0.0] * dimensions
    tokens = text.lower().split()
    if not tokens:
        return vector
    for token in tokens:
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class EmbeddingService:
    """Generate embeddings via the model layer with offline fallback."""

    def __init__(self, router: Any = None) -> None:
        self._router = router

    @property
    def router(self) -> Any:
        if self._router is None:
            try:
                from draftly.models.factory import build_embedding_router

                self._router = build_embedding_router()
            except Exception as exc:  # pragma: no cover - config dependent
                logger.warning("embedding_router_unavailable: %s", exc)
                self._router = False
        return self._router or None

    async def embed(self, text: str) -> list[float]:
        """Embed one text; deterministic fallback when no provider works."""
        router = self.router
        if router is not None:
            try:
                import asyncio
                vector = await asyncio.to_thread(router.embed, text)
                if vector:
                    return list(vector)
            except Exception as exc:
                logger.warning("embedder_fallback_used error=%s", exc)
        return _hash_embed(text)

    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        import asyncio

        router = self.router
        if router is not None and hasattr(router, "embed_batch"):
            try:
                vectors = await asyncio.to_thread(router.embed_batch, list(texts))
                if vectors and len(vectors) == len(texts):
                    return [list(v) for v in vectors]
            except Exception as exc:
                logger.warning("embedder_batch_fallback_used error=%s", exc)

        return list(
            await asyncio.gather(*(self.embed(text) for text in texts))
        )
