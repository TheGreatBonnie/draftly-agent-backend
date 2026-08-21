"""Tests for Amazon Bedrock provider."""

from __future__ import annotations

import pytest

from draftly.models.config import EmbeddingConfig, ModelConfig, ProviderConfig
from draftly.models.providers.bedrock import BedrockProvider


class TestBedrockProvider:
    """Tests for BedrockProvider."""

    def _provider(self) -> BedrockProvider:
        return BedrockProvider(ProviderConfig(name="bedrock", api_key=None, base_url="us-east-1"))

    def test_bedrock_provider_name(self) -> None:
        """Provider name should be 'bedrock'."""
        provider = self._provider()
        assert provider.name == "bedrock"

    def test_create_model_returns_bedrock_model(self) -> None:
        """create_model should return a Strands BedrockModel instance."""
        from strands.models import BedrockModel as StrandsBedrockModel

        provider = self._provider()
        config = ModelConfig(
            name="test-model",
            provider="bedrock",
            model_id="global.anthropic.claude-sonnet-4-6",
        )

        model = provider.create_model(config)

        assert isinstance(model, StrandsBedrockModel)

    def test_create_model_applies_config(self) -> None:
        """create_model should apply model_id, temperature, max_tokens from config."""
        provider = self._provider()
        config = ModelConfig(
            name="test-model",
            provider="bedrock",
            model_id="global.anthropic.claude-sonnet-4-6",
            temperature=0.5,
            max_tokens=2048,
        )

        model = provider.create_model(config)

        assert model.config["model_id"] == "global.anthropic.claude-sonnet-4-6"
        assert model.config["temperature"] == 0.5
        assert model.config["max_tokens"] == 2048

    def test_create_embedder_raises_not_implemented(self) -> None:
        """Bedrock doesn't have unified embeddings endpoint; raises NotImplementedError."""

        provider = self._provider()
        config = EmbeddingConfig(
            name="test-embedding",
            provider="bedrock",
            model_id="amazon.titan-embed-text-v2:0",
            dimensions=1024,
        )

        with pytest.raises(NotImplementedError):
            provider.create_embedder(config)


class TestBuildModelRouterWithBedrock:
    """Tests for factory registration of Bedrock provider and models."""

    def test_build_model_router_registers_bedrock_provider(self) -> None:
        """build_model_router should register 'bedrock' provider."""
        import os

        os.environ["AWS_REGION"] = "us-east-1"

        from draftly.models.factory import build_model_router

        router = build_model_router()

        assert "bedrock" in router.registry.providers()

    def test_build_model_router_registers_bedrock_models(self) -> None:
        """build_model_router should register Bedrock models."""
        import os

        os.environ["AWS_REGION"] = "us-east-1"

        from draftly.models.factory import build_model_router

        router = build_model_router()

        bedrock_models = router.registry.list_models()
        bedrock_model_names = [m.name for m in bedrock_models if m.provider == "bedrock"]

        assert "reasoning-bedrock-nova" in bedrock_model_names
        assert "fast-bedrock-nova" in bedrock_model_names
        assert "reasoning-bedrock-claude" in bedrock_model_names
        assert "fast-bedrock-claude" in bedrock_model_names
