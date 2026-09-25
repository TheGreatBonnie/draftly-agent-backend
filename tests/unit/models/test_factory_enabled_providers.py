"""Factory-level tests for strict provider gating via enabled_providers.

`build_model_router(enabled_providers={...})` must let callers restrict the
adaptive `route()` to a subset of providers (e.g. mantle-only) without
changing the registry itself.
"""

from __future__ import annotations

import os

import pytest

from draftly.models.factory import build_embedding_router, build_model_router
from draftly.models.router import NoCandidateError
from draftly.models.schemas import RoutingRequest, TaskType

ENABLED_PROVIDERS_ENV = "DRAFTLY_ENABLED_PROVIDERS"


def _route(provider_set: set[str] | None):
    os.environ["MANTLE_API_KEY"] = "test-key"
    router = build_model_router(enabled_providers=provider_set)
    return router.route(
        RoutingRequest(task_type=TaskType.REASONING, context_tokens=1000)
    )


def _route_env(value: str | None):
    os.environ["MANTLE_API_KEY"] = "test-key"
    if value is None:
        os.environ.pop(ENABLED_PROVIDERS_ENV, None)
    else:
        os.environ[ENABLED_PROVIDERS_ENV] = value
    router = build_model_router()
    return router.route(RoutingRequest(task_type=TaskType.REASONING, context_tokens=1000))


class TestEnabledProviders:
    def test_mantle_only_routes_within_providers(self) -> None:
        decision = _route({"mantle"})

        assert decision.provider == "mantle"

    def test_mantle_openai_only_routes_within_providers(self) -> None:
        decision = _route({"mantle-openai"})

        assert decision.provider == "mantle-openai"

    def test_mantle_and_mantle_openai_routes_within_both(self) -> None:
        decision = _route({"mantle", "mantle-openai"})

        assert decision.provider in {"mantle", "mantle-openai"}

    def test_openrouter_only_routes_within_providers(self) -> None:
        decision = _route({"openrouter"})

        assert decision.provider == "openrouter"

    def test_default_considers_every_provider(self) -> None:
        os.environ.pop(ENABLED_PROVIDERS_ENV, None)
        decision = _route(None)

        assert decision.provider in {
            "mantle",
            "requesty",
            "orcarouter",
            "openrouter",
            "nebius_token_factory",
        }

    def test_empty_set_routes_nowhere(self) -> None:
        with pytest.raises(NoCandidateError):
            _route(set())


class TestEnvEnabledProviders:
    def test_env_flag_restricts_to_mantle(self) -> None:
        decision = _route_env("mantle")

        assert decision.provider == "mantle"

    def test_env_flag_comma_separated(self) -> None:
        decision = _route_env("mantle, openrouter")

        assert decision.provider in {"mantle", "openrouter"}

    def test_env_flag_restricts_to_requesty(self) -> None:
        decision = _route_env("requesty")

        assert decision.provider == "requesty"

    def test_env_flag_empty_means_every_provider(self) -> None:
        decision = _route_env("")

        assert decision.provider in {
            "mantle",
            "requesty",
            "orcarouter",
            "openrouter",
            "nebius_token_factory",
        }

    def test_env_flag_unset_means_every_provider(self) -> None:
        decision = _route_env(None)

        assert decision.provider in {
            "mantle",
            "requesty",
            "orcarouter",
            "openrouter",
            "nebius_token_factory",
        }

    def test_explicit_param_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENABLED_PROVIDERS_ENV, "openrouter")
        decision = _route({"mantle"})

        assert decision.provider == "mantle"


class TestEnabledProvidersRegistryUnchanged:
    def test_registry_still_holds_all_providers(self) -> None:
        os.environ["MANTLE_API_KEY"] = "test-key"
        router = build_model_router(enabled_providers={"mantle"})

        providers = {p.provider for p in router.registry.list_models()}

        assert "requesty" in providers
        assert "orcarouter" in providers
        assert "openrouter" in providers


class TestEmbeddingProviderGating:
    """build_embedding_router honors the same provider gate as route().

    Unlike the chat registry (which keeps every provider registered and
    filters at route time), the embedding registry only registers enabled
    providers: ``EmbeddingRouter`` iterates ``list_embedding_models()``
    directly with no separate filter.
    """

    EMBEDDING_KEYS = (
        "REQUESTY_API_KEY",
        "ORCAROUTER_API_KEY",
        "OPENROUTER_API_KEY",
        "NEBIUS_TOKEN_FACTORY_API_KEY",
    )

    @pytest.fixture(autouse=True)
    def _embedding_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in self.EMBEDDING_KEYS:
            monkeypatch.setenv(var, "test-key")
        monkeypatch.delenv(ENABLED_PROVIDERS_ENV, raising=False)
        monkeypatch.delenv("EMBEDDING_MODEL_ID", raising=False)

    def test_param_restricts_to_enabled_provider(self) -> None:
        ranked = build_embedding_router(
            enabled_providers={"nebius_token_factory"}
        ).registry.list_embedding_models()

        assert [m.provider for m in ranked] == ["nebius_token_factory"]

    def test_env_flag_restricts_embedding_providers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENABLED_PROVIDERS_ENV, "nebius_token_factory")

        ranked = build_embedding_router().registry.list_embedding_models()

        assert [m.provider for m in ranked] == ["nebius_token_factory"]

    def test_param_keeps_multiple_enabled_providers(self) -> None:
        ranked = build_embedding_router(
            enabled_providers={"requesty", "orcarouter"}
        ).registry.list_embedding_models()

        assert {m.provider for m in ranked} == {"requesty", "orcarouter"}

    def test_explicit_param_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENABLED_PROVIDERS_ENV, "nebius_token_factory")

        ranked = build_embedding_router(
            enabled_providers={"openrouter"}
        ).registry.list_embedding_models()

        assert [m.provider for m in ranked] == ["openrouter"]

    def test_gate_excluding_every_embedder_raises(self) -> None:
        with pytest.raises(RuntimeError, match="No embedding provider is enabled"):
            build_embedding_router(enabled_providers={"mantle"})

    def test_no_gate_registers_all_embedding_providers(self) -> None:
        ranked = build_embedding_router().registry.list_embedding_models()

        assert {m.provider for m in ranked} == {
            "openrouter",
            "requesty",
            "orcarouter",
            "nebius_token_factory",
        }
