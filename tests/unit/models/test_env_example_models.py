"""Registration contract after the live probe (make probe-models evidence).

Survivors keep their .env.example env wiring; models the probe proved dead
or capability-broken are pruned or corrected accordingly.
"""

from __future__ import annotations

import pytest

from draftly.models.factory import build_model_router

#: (registry name, provider, env var, expected default slug)
SURVIVORS: tuple[tuple[str, str, str, str], ...] = (
    (
        "reasoning-requesty-v4-pro",
        "requesty",
        "REQUESTY_DEEPSEEK_V4_PRO_MODEL",
        "deepseek/deepseek-v4-pro-0813",
    ),
    (
        "fast-requesty-luna",
        "requesty",
        "REQUESTY_OPENAI_5.6_LUNA_MODEL",
        "openai/gpt-5.6-luna",
    ),
    (
        "reasoning-orca-v4-pro",
        "orcarouter",
        "ORCA_DEEPSEEK_V4_PRO_MODEL",
        "deepseek/deepseek-v4-pro",
    ),
    (
        "fast-orca-v4-pro-free",
        "orcarouter",
        "ORCA_DEEPSEEK_V4_PRO_FREE_MODEL",
        "deepseek/deepseek-v4-pro-free",
    ),
    (
        "fast-orca-v4-flash",
        "orcarouter",
        "ORCA_DEEPSEEK_V4_FLASH_MODEL",
        "deepseek/deepseek-v4-flash",
    ),
    (
        "fast-orca-v4-flash-free",
        "orcarouter",
        "ORCA_DEEPSEEK_V4_FLASH_FREE_MODEL",
        "deepseek/deepseek-v4-flash-free",
    ),
    (
        "fast-orca-v4-flash-0731",
        "orcarouter",
        "ORCA_DEEPSEEK_V4_FLASH_0731_MODEL",
        "deepseek/deepseek-v4-flash-0731",
    ),
    (
        "fast-orca-luna",
        "orcarouter",
        "ORCA_OPENAI_5.6_LUNA_MODEL",
        "openai/gpt-5.6-luna",
    ),
    (
        "nemotron-nano-fast",
        "nebius_token_factory",
        "NEMOTRON_NANO_MODEL_ID",
        "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    ),
    (
        "nemotron-super-research",
        "nebius_token_factory",
        "NEMOTRON_SUPER_MODEL_ID",
        "nvidia/nemotron-3-super-120b-a12b",
    ),
    (
        "nemotron-ultra-doc",
        "nebius_token_factory",
        "NEMOTRON_ULTRA_MODEL_ID",
        "nvidia/Nemotron-3-Ultra-550b-a55b",
    ),
)

#: Probe verdicts: 404/410 on every probe (EOL or never deployed).
PRUNED: tuple[str, ...] = (
    "reasoning-nvidia-glm-5-2",
    "reasoning-nvidia-kimi-k2-6",
    "fast-nvidia-minimax-m3",
    "fast-nvidia-deepseek-v4-pro",
    "fast-nvidia-deepseek-v4-flash",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for _, _, env_var, _ in SURVIVORS:
        monkeypatch.delenv(env_var, raising=False)


class TestSurvivorModelsRegistered:
    @pytest.mark.parametrize("name,provider,env_var,default_slug", SURVIVORS)
    def test_model_registered_with_expected_default(
        self,
        name: str,
        provider: str,
        env_var: str,
        default_slug: str,
    ) -> None:
        config = build_model_router().registry.get_model(name)

        assert config.provider == provider
        assert config.model_id == default_slug

    @pytest.mark.parametrize("name,provider,env_var,default_slug", SURVIVORS)
    def test_env_var_overrides_default(
        self,
        name: str,
        provider: str,
        env_var: str,
        default_slug: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(env_var, "override/test-slug")

        config = build_model_router().registry.get_model(name)

        assert config.model_id == "override/test-slug"

    def test_all_survivors_declare_tool_calling(self) -> None:
        registry = build_model_router().registry

        for name, *_ in SURVIVORS:
            assert "tool_calling" in registry.get_model(name).capabilities


class TestNoOutputTokenCaps:
    def test_all_registered_models_are_uncapped(self) -> None:
        registry = build_model_router().registry

        for config in registry.list_models():
            assert config.max_tokens is None, (
                f"{config.name} imposes max_tokens={config.max_tokens!r}"
            )


class TestPrunedModelsAbsent:
    @pytest.mark.parametrize("name", PRUNED)
    def test_dead_model_not_registered(self, name: str) -> None:
        registry = build_model_router().registry

        with pytest.raises(KeyError):
            registry.get_model(name)

    def test_nvidia_has_no_registered_models(self) -> None:
        router = build_model_router()

        assert [m for m in router.registry.list_models() if m.provider == "nvidia"] == []


class TestProbeCorrectedCapabilities:
    """Structured-output stripped where the probe proved it unsupported."""

    def test_requesty_v4_pro_has_no_structured_output(self) -> None:
        config = build_model_router().registry.get_model("reasoning-requesty-v4-pro")

        assert "structured_output" not in config.capabilities

    @pytest.mark.parametrize(
        "name",
        ["fast-orca-v4-flash-free", "fast-orca-v4-pro-free"],
    )
    def test_orca_free_tier_has_no_structured_output(self, name: str) -> None:
        config = build_model_router().registry.get_model(name)

        assert "structured_output" not in config.capabilities


class TestProbeDrivenOrcaRanking:
    def test_fast_orca_luna_is_top_orca_fast_candidate(self) -> None:
        router = build_model_router()
        orca = [
            m for m in router.registry.list_models(capability="tool_calling")
            if m.provider == "orcarouter"
        ]
        names = [m.name for m in orca]

        assert names.index("fast-orca-luna") < names.index("fast-orca-v4-flash")


class TestEmbeddingProviderRanking:
    """Only the OpenRouter embedder passed the live probe."""

    def test_openrouter_embedder_ranks_first(self) -> None:
        from draftly.models.factory import build_embedding_router

        ranked = build_embedding_router().registry.list_embedding_models()

        assert ranked[0].provider == "openrouter"
