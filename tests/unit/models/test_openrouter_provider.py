"""Tests for OpenRouter provider and Ox Alpha factory registration."""

from __future__ import annotations

import pytest

from draftly.models.config import ModelConfig, ProviderConfig
from draftly.models.factory import build_model_router
from draftly.models.providers.openrouter import OpenRouterProvider


class TestOpenRouterProvider:
    """Tests for OpenRouterProvider."""

    def _provider(self) -> OpenRouterProvider:
        return OpenRouterProvider(
            ProviderConfig(
                name="openrouter",
                api_key="test-key",
                base_url=None,
            )
        )

    def test_openrouter_provider_name(self) -> None:
        """Provider name should be 'openrouter'."""
        provider = self._provider()
        assert provider.name == "openrouter"

    def test_create_model_returns_openai_model(self) -> None:
        """create_model should return a Strands OpenAIModel instance."""
        from strands.models import OpenAIModel as StrandsOpenAIModel

        provider = self._provider()
        config = ModelConfig(
            name="test-model",
            provider="openrouter",
            model_id="stealth/ox-alpha",
        )

        model = provider.create_model(config)

        assert isinstance(model, StrandsOpenAIModel)

    def test_create_model_defaults_to_openrouter_base_url(self) -> None:
        """create_model should fall back to the public OpenRouter API URL."""
        provider = self._provider()
        config = ModelConfig(
            name="test-model",
            provider="openrouter",
            model_id="stealth/ox-alpha",
        )

        model = provider.create_model(config)

        assert model.client_args["base_url"] == "https://openrouter.ai/api/v1"


class TestDisabledStealthOxAlpha:
    """Development was paused for stealth/ox-alpha; registration is disabled."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def test_fast_openrouter_not_registered(self) -> None:
        """fast-openrouter failed tool and structured probes; pruned."""
        registry = build_model_router().registry

        with pytest.raises(KeyError):
            registry.get_model("fast-openrouter")

    def test_reasoning_openrouter_ox_alpha_not_registered(self) -> None:
        """Ox Alpha is disabled: its structured probe returned prose instead of
        schema JSON. Mirrors fast-openrouter's pruning until re-validated."""
        registry = build_model_router().registry

        with pytest.raises(KeyError):
            registry.get_model("reasoning-openrouter-ox-alpha")

    def test_ox_alpha_not_ranked_among_reasoning_candidates(self) -> None:
        """With Ox Alpha disabled it must not appear in reasoning routing."""
        router = build_model_router()

        candidates = router.registry.list_models(capability="reasoning")
        names = [model.name for model in candidates]

        assert "reasoning-openrouter-ox-alpha" not in names
