from types import SimpleNamespace
from unittest.mock import MagicMock

from draftly.models.embeddings import OpenAICompatibleEmbedder


class _FakeOpenAI:
    def __init__(self):
        self.calls = []
        self._embeddings = self._Embeddings()
        self._embeddings._calls = self.calls

    class _Embeddings:
        def create(self, *, model, input, dimensions=None):
            # record the inputs we sent; return fake vectors in order
            outer = self
            holder = getattr(outer, "_calls", None)
            if holder is not None:
                holder.append(
                    {"model": model, "input": input, "dimensions": dimensions}
                )
            return SimpleNamespace(data=[
                SimpleNamespace(embedding=[float(i), float(i + 1)])
                for i, _ in enumerate(input)
            ])

    # NOTE: must be a property, NOT a method — embed_queries accesses
    # self._client.embeddings.create(...) without calling embeddings().
    @property
    def embeddings(self):
        return self._embeddings


def test_embed_queries_sends_all_texts_in_one_call():
    embedder = OpenAICompatibleEmbedder(
        api_key="k", base_url="https://x", model_id="text-embedding-3-small",
    )
    embedder._client = _FakeOpenAI()
    out = embedder.embed_queries(["a", "b", "c"])
    assert len(out) == 3
    assert out[0] == [0.0, 1.0]
    assert out[2] == [2.0, 3.0]


def test_embed_forwards_dimensions_when_configured():
    embedder = OpenAICompatibleEmbedder(
        api_key="k", base_url="https://x", model_id="Qwen/Qwen3-Embedding-8B",
        dimensions=1536,
    )
    client = _FakeOpenAI()
    embedder._client = client
    out = embedder.embed_queries(["a", "b"])
    assert len(out) == 2
    assert client.calls[0]["dimensions"] == 1536
    assert client.calls[0]["model"] == "Qwen/Qwen3-Embedding-8B"


def test_embed_omits_dimensions_when_not_configured():
    embedder = OpenAICompatibleEmbedder(
        api_key="k", base_url="https://x", model_id="text-embedding-3-small",
    )
    client = _FakeOpenAI()
    embedder._client = client
    embedder.embed_queries(["a"])
    assert client.calls[0]["dimensions"] is None


def _embedder_returning(n_vecs):
    e = MagicMock()
    e.embed_queries.return_value = [[float(i), float(i + 1)] for i in range(n_vecs)]
    return e


def test_router_embed_batch_uses_batched_embedder():
    from draftly.models.embeddings import EmbeddingRouter

    embedder = _embedder_returning(3)
    router = EmbeddingRouter(registry=MagicMock(), health=MagicMock())
    router._ordered_candidates = lambda: [
        SimpleNamespace(
            provider="openrouter", model_id="text-embedding-3-small",
            priority=1, dimensions=2,
        )
    ]
    registry = MagicMock()
    registry.get_provider.return_value = MagicMock(
        is_enabled=lambda: True,
        create_embedder=lambda cfg: embedder,
    )
    router.registry = registry
    router.health = MagicMock()
    router.health.get.return_value = MagicMock(
        available=lambda: True, record_failure=lambda f: None, record_success=lambda: None,
    )

    out = router.embed_batch(["a", "b", "c"])

    assert len(out) == 3
    embedder.embed_queries.assert_called_once_with(["a", "b", "c"])
    # embed_query (per-text) must NOT be used for the batch path
    embedder.embed_query.assert_not_called()


def test_embed_batch_logs_once_not_per_text(monkeypatch):
    import structlog
    from structlog.testing import capture_logs

    import draftly.models.embeddings as emb_mod
    from draftly.models.embeddings import EmbeddingRouter

    embedder = _embedder_returning(3)
    router = EmbeddingRouter(registry=MagicMock(), health=MagicMock())

    class Cfg:
        provider = "openrouter"
        model_id = "text-embedding-3-small"
        priority = 1
        dimensions = 2  # must match _embedder_returning's 2-dim vectors

    router._ordered_candidates = lambda: [Cfg()]
    registry = MagicMock()
    registry.get_provider.return_value = MagicMock(
        is_enabled=lambda: True, create_embedder=lambda cfg: embedder,
    )
    router.registry = registry
    router.health = MagicMock()
    router.health.get.return_value = MagicMock(
        available=lambda: True, record_failure=lambda f: None, record_success=lambda: None,
    )

    # The module logger is cached under cache_logger_on_first_use, so patch
    # emb_mod.logger with a fresh proxy that resolves against capture_logs'
    # temporary processors (mirrors test_llm_generate_logs_routing).
    with capture_logs() as logs:
        monkeypatch.setattr(
            emb_mod, "logger", structlog.get_logger("test.embed_batch_logging"),
        )
        router.embed_batch(["a", "b", "c"])

    # embed_batch logs via %s-format positional calls, so structlog renders
    # the interpolated event into `event` (e.g. "embedding batch attempting
    # provider=openrouter model=... count=3"). Match on the marker substring.
    batch_attempts = [e for e in logs if "embedding batch attempting" in e.get("event", "")]
    batch_resolved = [e for e in logs if "embedding batch resolved" in e.get("event", "")]
    per_text = [e for e in logs if "embedding router attempting" in e.get("event", "")]
    assert len(batch_attempts) == 1
    assert len(batch_resolved) == 1
    assert per_text == []
