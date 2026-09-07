"""Role-aware per-task model resolution."""

from unittest.mock import MagicMock

import pytest

from draftly.integrations.strands.models import (
    RoleAwareModelResolver,
    resolve_model_for_role,
)


@pytest.fixture()
def router():
    from draftly.models.config import ModelConfig
    from draftly.models.health import ProviderHealthRegistry
    from draftly.models.registry import ModelRegistry
    from draftly.models.router import ModelRouter

    provider = MagicMock()
    provider.name = "openrouter"
    provider.create_model.side_effect = lambda cfg: f"MODEL<{cfg.name}>"

    reg = ModelRegistry()
    reg.register_provider(provider)
    reg.register_model(ModelConfig(
        name="writer-model", provider="openrouter", model_id="org/w",
        capabilities=("reasoning", "tool_calling", "structured_output"),
        priority=1,
    ))
    # Cheap pricing lets the FAST profile (w_cost=0.25) prefer the
    # curator over the unpriced writer (sentinel cost scores 0.0);
    # unpriced-vs-unpriced would tie and fall through to priority,
    # picking the writer for every uncapped task type.
    reg.register_model(ModelConfig(
        name="curator-model", provider="openrouter", model_id="org/c",
        priority=2,
        input_cost_per_1m_tokens=0.50,
        output_cost_per_1m_tokens=1.0,
    ))
    router = ModelRouter(registry=reg, health=ProviderHealthRegistry())
    # Proven research track record for the writer: the reasoning profile
    # (RESEARCH) weights history/reliability, so github_intelligence must
    # resolve the writer despite the curator's cost edge.
    for _ in range(40):
        router.stats_store.record_outcome(
            "research", "writer-model", success=True, latency_ms=1000.0
        )
    return router


def test_real_roles_map_through_route(router):
    """documentation_engineer must NOT fall back to SUPPORT."""
    resolver = RoleAwareModelResolver(router)
    model = resolver.for_role("documentation_engineer")
    assert model == "MODEL<writer-model>"


def test_memory_curator_resolves_fast_profile(router):
    resolver = RoleAwareModelResolver(router)
    assert resolver.for_role("memory_curator") == "MODEL<curator-model>"


def test_unknown_role_raises_not_silently_defaults(router):
    resolver = RoleAwareModelResolver(router)
    with pytest.raises(ValueError, match="Unknown role"):
        resolver.for_role("intern")


def test_helper_passthrough_non_resolver():
    assert resolve_model_for_role("PLAIN-MODEL", "support_engineer") == "PLAIN-MODEL"


def test_helper_resolves_via_resolver(router):
    assert resolve_model_for_role(
        RoleAwareModelResolver(router), "github_intelligence"
    ) == "MODEL<writer-model>"


def test_for_role_with_decision_returns_model_and_decision(router):
    resolver = RoleAwareModelResolver(router)
    model, decision = resolver.for_role_with_decision("documentation_engineer")
    assert model == "MODEL<writer-model>"
    assert decision.selected_model == "writer-model"
    assert decision.task_type == "documentation_generation"


def test_for_role_publishes_decision_to_run_scoped_sink(router):
    captured = []
    resolver = RoleAwareModelResolver(
        router,
        decision_sink=lambda role, decision: captured.append((role, decision)),
    )

    assert resolver.for_role("github_intelligence") == "MODEL<writer-model>"
    assert len(captured) == 1
    assert captured[0][0] == "github_intelligence"
    assert captured[0][1].task_type == "research"


def test_for_role_delegates_to_for_role_with_decision(router):
    resolver = RoleAwareModelResolver(router)
    model, _decision = resolver.for_role_with_decision("github_intelligence")
    assert resolver.for_role("github_intelligence") == model


def test_for_role_with_decision_degrades_offline():
    from draftly.models.health import ProviderHealthRegistry
    from draftly.models.registry import ModelRegistry
    from draftly.models.router import ModelRouter

    empty = ModelRouter(registry=ModelRegistry(), health=ProviderHealthRegistry())
    resolver = RoleAwareModelResolver(empty)
    assert resolver.for_role_with_decision("support_engineer") == (None, None)
    assert resolver.for_role("support_engineer") is None
