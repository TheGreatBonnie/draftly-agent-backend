"""Tests for the Nebius Token Factory provider.

Token Factory exposes an OpenAI-compatible endpoint
(``https://api.tokenfactory.nebius.com/v1``) serving NVIDIA Nemotron
models, so the provider mirrors the ``RequestyProvider`` / ``OpenRouter``
pattern: require an API key, construct a ``strands.OpenAIModel``, and
expose an OpenAI-compatible embedder with a ``dimensions`` passthrough
(the embedding tier is ``Qwen/Qwen3-Embedding-8B`` at 1536 dims).
"""

from __future__ import annotations

import pytest

from draftly.models.config import EmbeddingConfig, ModelConfig, ProviderConfig
from draftly.models.providers.nebius_token_factory import NebiusTokenFactoryProvider

TF_BASE_URL = "https://api.tokenfactory.nebius.com/v1"


class TestNebiusTokenFactoryProvider:
    """Provider construction contract."""

    def _provider(self, api_key: str | None = "test-key") -> NebiusTokenFactoryProvider:
        return NebiusTokenFactoryProvider(
            ProviderConfig(
                name="nebius_token_factory",
                api_key=api_key,
                base_url=None,
            )
        )

    def test_provider_name(self) -> None:
        assert self._provider().name == "nebius_token_factory"

    def test_create_model_requires_api_key(self) -> None:
        provider = self._provider(api_key=None)
        config = ModelConfig(
            name="nemotron-nano-fast",
            provider="nebius_token_factory",
            model_id="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
        )

        with pytest.raises(ValueError, match="NEBIUS_TOKEN_FACTORY_API_KEY"):
            provider.create_model(config)

    def test_create_model_returns_openai_model(self) -> None:
        from strands.models import OpenAIModel as StrandsOpenAIModel

        provider = self._provider()
        config = ModelConfig(
            name="nemotron-nano-fast",
            provider="nebius_token_factory",
            model_id="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
        )

        model = provider.create_model(config)

        assert isinstance(model, StrandsOpenAIModel)

    def test_create_model_defaults_to_token_factory_base_url(self) -> None:
        provider = self._provider()
        config = ModelConfig(
            name="nemotron-super-research",
            provider="nebius_token_factory",
            model_id="nvidia/nemotron-3-super-120b-a12b",
        )

        model = provider.create_model(config)

        assert model.client_args["base_url"] == TF_BASE_URL

    def test_create_model_applies_config(self) -> None:
        provider = self._provider()
        config = ModelConfig(
            name="nemotron-ultra-doc",
            provider="nebius_token_factory",
            model_id="nvidia/NVIDIA-Nemotron-3-Ultra-550b-a55b",
            temperature=0.4,
            max_tokens=4096,
        )

        model = provider.create_model(config)

        assert model.config["model_id"] == "nvidia/NVIDIA-Nemotron-3-Ultra-550b-a55b"
        assert model.config["params"]["temperature"] == 0.4
        assert model.config["params"]["max_tokens"] == 4096

    def test_create_model_honors_custom_base_url(self) -> None:
        provider = NebiusTokenFactoryProvider(
            ProviderConfig(
                name="nebius_token_factory",
                api_key="test-key",
                base_url="https://proxy.example.com/v1",
            )
        )
        config = ModelConfig(
            name="nemotron-nano-fast",
            provider="nebius_token_factory",
            model_id="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
        )

        model = provider.create_model(config)

        assert model.client_args["base_url"] == "https://proxy.example.com/v1"

    def test_create_embedder_requires_api_key(self) -> None:
        provider = self._provider(api_key=None)
        config = EmbeddingConfig(
            name="embedding-nebius-token-factory",
            provider="nebius_token_factory",
            model_id="Qwen/Qwen3-Embedding-8B",
            dimensions=1536,
        )

        with pytest.raises(ValueError, match="NEBIUS_TOKEN_FACTORY_API_KEY"):
            provider.create_embedder(config)

    def test_create_embedder_returns_openai_compatible_embedder(self) -> None:
        from draftly.models.embeddings import OpenAICompatibleEmbedder

        provider = self._provider()
        config = EmbeddingConfig(
            name="embedding-nebius-token-factory",
            provider="nebius_token_factory",
            model_id="Qwen/Qwen3-Embedding-8B",
            dimensions=1536,
        )

        embedder = provider.create_embedder(config)

        assert isinstance(embedder, OpenAICompatibleEmbedder)
        assert embedder.model_id == "Qwen/Qwen3-Embedding-8B"
        assert embedder.dimensions == 1536
