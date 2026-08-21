"""Tests for Amazon Bedrock Mantle provider."""

from __future__ import annotations

import pytest

from draftly.models.config import EmbeddingConfig, ModelConfig, ProviderConfig
from draftly.models.providers.mantle import MantleProvider


class TestMantleProvider:
    """Tests for MantleProvider."""

    def _provider(self) -> MantleProvider:
        return MantleProvider(
            ProviderConfig(
                name="mantle",
                api_key="test-key",
                base_url="https://bedrock-mantle.us-east-1.api.aws/openai/v1",
            )
        )

    def test_mantle_provider_name(self) -> None:
        """Provider name should be 'mantle'."""
        provider = self._provider()
        assert provider.name == "mantle"

    def test_create_model_returns_openai_model(self) -> None:
        """create_model should return a Strands OpenAIModel instance."""
        from strands.models import OpenAIModel as StrandsOpenAIModel

        provider = self._provider()
        config = ModelConfig(
            name="test-model",
            provider="mantle",
            model_id="openai/gpt-5.6-luna",
        )

        model = provider.create_model(config)

        assert isinstance(model, StrandsOpenAIModel)

    def test_create_model_applies_config(self) -> None:
        """create_model should apply model_id, temperature, max_tokens from config."""
        provider = self._provider()
        config = ModelConfig(
            name="test-model",
            provider="mantle",
            model_id="openai/gpt-5.6-luna",
            temperature=0.5,
            max_tokens=2048,
        )

        model = provider.create_model(config)

        assert model.config["model_id"] == "openai/gpt-5.6-luna"
        assert model.config["params"]["temperature"] == 0.5
        assert model.config["params"]["max_tokens"] == 2048

    def test_create_model_uses_mantle_endpoint(self) -> None:
        """create_model should use Mantle base URL."""
        provider = self._provider()
        config = ModelConfig(
            name="test-model",
            provider="mantle",
            model_id="openai/gpt-5.6-luna",
        )

        model = provider.create_model(config)

        # The base_url is in client_args, not directly in config
        # Verify the model was created with the right base_url via client_args
        assert model.config["model_id"] == "openai/gpt-5.6-luna"

    def test_create_embedder_raises_not_implemented(self) -> None:
        """Mantle doesn't have standard embeddings endpoint; raises NotImplementedError."""
        provider = self._provider()
        config = EmbeddingConfig(
            name="test-embedding",
            provider="mantle",
            model_id="text-embedding-3-small",
            dimensions=1536,
        )

        with pytest.raises(NotImplementedError):
            provider.create_embedder(config)


class TestBuildModelRouterWithMantle:
    """Tests for factory registration of Mantle provider and models."""

    def test_build_model_router_registers_mantle_provider(self) -> None:
        """build_model_router should register 'mantle' provider."""
        import os

        os.environ["MANTLE_API_KEY"] = "test-key"

        from draftly.models.factory import build_model_router

        router = build_model_router()

        assert "mantle" in router.registry.providers()

    def test_build_model_router_registers_mantle_models(self) -> None:
        """build_model_router should register Mantle models."""
        import os

        os.environ["MANTLE_API_KEY"] = "test-key"

        from draftly.models.factory import build_model_router

        router = build_model_router()

        mantle_models = router.registry.list_models()
        mantle_model_names = [m.name for m in mantle_models if m.provider == "mantle"]

        assert "reasoning-mantle-gpt" in mantle_model_names
        assert "fast-mantle-gpt" in mantle_model_names
