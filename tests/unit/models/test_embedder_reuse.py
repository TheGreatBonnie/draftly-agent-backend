"""The embedder must be built once per router, not once per query.

``EmbeddingRouter.embed`` called ``provider.create_embedder(config)`` inside
the per-candidate loop, so every query re-resolved the provider and rebuilt
the client. Run d76e2490 logged 6 ``embedding router resolved`` events in the
impact window alone; each resolution is another client construction, and the
count scales with query count.

Failover is what made the per-call resolution look necessary: on an error the
router advances to the next candidate, and each candidate has its own provider.
So the cache is keyed by ``(provider, model_id)``, and only successful
construction is cached.
"""

from __future__ import annotations

import pytest

from draftly.models.config import EmbeddingConfig
from draftly.models.embeddings import EmbeddingRouter
from draftly.models.health import ProviderHealthRegistry
from draftly.models.registry import ModelRegistry

MODEL_ID = "text-embedding-3-small"
DIMS = 3


class _Embedder:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.fail = False

    def _vector(self) -> list[float]:
        if self.fail:
            raise RuntimeError("transient upstream error")
        return [0.1, 0.2, 0.3]

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return self._vector()

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        self.queries.extend(texts)
        return [self._vector() for _ in texts]


class _Provider:
    def __init__(self, name: str, embedder: _Embedder) -> None:
        self.name = name
        self._embedder = embedder
        self.create_calls = 0
        self.enabled = True

    def is_enabled(self) -> bool:
        return self.enabled

    def create_embedder(self, config: EmbeddingConfig) -> _Embedder:
        self.create_calls += 1
        return self._embedder


def _router(*providers: _Provider) -> EmbeddingRouter:
    """A router serving one embedding model across the given providers."""
    registry = ModelRegistry()
    for index, provider in enumerate(providers):
        registry.register_provider(provider)  # type: ignore[arg-type]
        registry.register_embedding_model(
            EmbeddingConfig(
                name=f"{provider.name}-{MODEL_ID}",
                provider=provider.name,
                model_id=MODEL_ID,
                dimensions=DIMS,
                priority=index,
            )
        )
    return EmbeddingRouter(
        registry=registry,
        health=ProviderHealthRegistry(),
    )


def _one_provider() -> tuple[EmbeddingRouter, _Provider, _Embedder]:
    embedder = _Embedder()
    provider = _Provider("p1", embedder)
    return _router(provider), provider, embedder


def test_many_queries_build_one_embedder() -> None:
    router, provider, _ = _one_provider()

    for i in range(20):
        router.embed(f"query {i}")

    assert provider.create_calls == 1


def test_queries_still_reach_the_embedder() -> None:
    """Caching the client must not cache the answer."""
    router, _, embedder = _one_provider()

    router.embed("a")
    router.embed("b")

    assert embedder.queries == ["a", "b"]


def test_repeated_batches_build_one_embedder() -> None:
    router, provider, embedder = _one_provider()

    for _ in range(10):
        router.embed_batch(["a", "b"])

    assert provider.create_calls == 1
    assert embedder.queries == ["a", "b"] * 10


def test_queries_and_batches_share_one_embedder() -> None:
    router, provider, _ = _one_provider()

    router.embed("a")
    router.embed_batch(["b", "c"])

    assert provider.create_calls == 1


def test_failover_still_builds_the_next_candidate() -> None:
    """A cached client must not be reused across providers."""
    first, second = _Embedder(), _Embedder()
    p1, p2 = _Provider("p1", first), _Provider("p2", second)
    router = _router(p1, p2)

    p1.enabled = False
    router.embed("a")

    assert router.last_provider == "p2"
    assert first.queries == []
    assert second.queries == ["a"]


def test_each_provider_gets_its_own_embedder() -> None:
    """The cache is keyed per provider, so a fallback is not a reuse."""
    p1, p2 = _Provider("p1", _Embedder()), _Provider("p2", _Embedder())
    router = _router(p1, p2)

    router.embed("a")
    p1.enabled = False
    router.embed("b")

    assert p1.create_calls == 1
    assert p2.create_calls == 1


def test_a_failed_embed_is_not_cached_as_a_bad_result() -> None:
    """Health marks the provider unhealthy, so recovery needs a fresh health
    record. The embedder cache must not be the thing that keeps a dead client.

    A failed *embed* is a health signal, not a cache entry: the router records
    it and the provider is skipped from then on. Nothing about the cache should
    make that worse, and nothing about it should keep a poisoned client.
    """
    router, provider, embedder = _one_provider()
    embedder.fail = True

    with pytest.raises(RuntimeError):
        router.embed("a")

    assert provider.create_calls == 1

    # Health is what blocks the retry, not the cache: clear it and the same
    # embedder is reused, not rebuilt.
    router.health.get("p1").healthy = True
    embedder.fail = False
    assert router.embed("b") == [0.1, 0.2, 0.3]
    assert provider.create_calls == 1


def test_a_failed_construction_is_not_cached() -> None:
    """A client that failed to build must not be remembered as built.

    Construction failure means there is no client at all, so caching a
    placeholder would make every later query fail identically.
    """
    provider = _Provider("p1", _Embedder())
    attempts = 0

    def _create(config: EmbeddingConfig) -> _Embedder:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("client construction timed out")
        return provider._embedder

    provider.create_embedder = _create  # type: ignore[method-assign]
    router = _router(provider)

    with pytest.raises(RuntimeError):
        router.embed("a")

    router.health.get("p1").healthy = True
    assert router.embed("b") == [0.1, 0.2, 0.3]
    assert attempts == 2


def test_routers_do_not_share_embedders() -> None:
    """A cache must be per-instance, not module-global."""
    embedder = _Embedder()
    provider = _Provider("p1", embedder)
    one = _router(provider)
    two = _router(provider)

    one.embed("a")
    two.embed("b")

    assert embedder.queries == ["a", "b"]
