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


class TestBuildModelRouterWithOxAlpha:
    """Tests for factory registration of the stealth/ox-alpha model."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENROUTER_OX_ALPHA_MODEL", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def test_build_model_router_registers_ox_alpha(self) -> None:
        """build_model_router should register the Ox Alpha model."""
        router = build_model_router()

        config = router.registry.get_model("reasoning-openrouter-ox-alpha")

        assert config.provider == "openrouter"
        assert config.model_id == "stealth/ox-alpha"

    def test_build_model_router_registers_ox_alpha_capabilities(self) -> None:
        """Ox Alpha: reasoning + tool calling only (structured failed probe)."""
        router = build_model_router()

        config = router.registry.get_model("reasoning-openrouter-ox-alpha")

        assert set(config.capabilities) == {
            "reasoning",
            "tool_calling",
        }

    def test_fast_openrouter_not_registered(self) -> None:
        """fast-openrouter failed tool and structured probes; pruned."""
        registry = build_model_router().registry

        with pytest.raises(KeyError):
            registry.get_model("fast-openrouter")

    def test_build_model_router_registers_ox_alpha_routing_metadata(self) -> None:
        """Ox Alpha should carry its declared window, free pricing, priority."""
        router = build_model_router()

        config = router.registry.get_model("reasoning-openrouter-ox-alpha")

        assert config.priority == 6
        assert config.context_window == 1_048_576
        assert config.input_cost_per_1m_tokens == 0.0
        assert config.output_cost_per_1m_tokens == 0.0

    def test_ox_alpha_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OPENROUTER_OX_ALPHA_MODEL should override the default slug."""
        monkeypatch.setenv("OPENROUTER_OX_ALPHA_MODEL", "stealth/ox-beta")

        router = build_model_router()

        config = router.registry.get_model("reasoning-openrouter-ox-alpha")
        assert config.model_id == "stealth/ox-beta"

    def test_ox_alpha_outranks_lower_priority_reasoning_candidates(self) -> None:
        """Priority 6 should rank Ox Alpha ahead of requesty candidates."""
        router = build_model_router()

        candidates = router.registry.list_models(capability="reasoning")
        names = [model.name for model in candidates]

        assert names.index("reasoning-openrouter-ox-alpha") < names.index(
            "reasoning-requesty"
        )
