from unittest.mock import MagicMock

import pytest

from draftly.memory.embeddings import EmbeddingService


@pytest.mark.asyncio
async def test_embed_batch_uses_router_batch_when_available():
    router = MagicMock()
    router_vectors = [[1.0, 2.0], [3.0, 4.0]]
    router.embed_batch.return_value = router_vectors
    svc = EmbeddingService(router=router)

    out = await svc.embed_batch(["a", "b"])

    assert out == router_vectors
    router.embed_batch.assert_called_once_with(["a", "b"])


@pytest.mark.asyncio
async def test_embed_batch_falls_back_per_text_on_router_error():
    class Router:
        def embed(self, text):
            return [len(text), 1.0]

        def embed_batch(self, texts):
            raise RuntimeError("provider down")

    svc = EmbeddingService(router=Router())

    out = await svc.embed_batch(["a", "bb"])

    assert out == [[1.0, 1.0], [2.0, 1.0]]
