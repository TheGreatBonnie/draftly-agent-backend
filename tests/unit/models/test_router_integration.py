"""Integration tests for the adaptive router facade."""

from unittest.mock import MagicMock

import pytest

from draftly.models.config import ModelConfig
from draftly.models.health import ProviderHealthRegistry
from draftly.models.policies import RoutingPolicy
from draftly.models.registry import ModelRegistry
from draftly.models.router import ModelRouter, NoCandidateError
from draftly.models.schemas import ROLE_TO_TASK_TYPE, RoutingRequest, TaskType


def _provider():
    provider = MagicMock()
    provider.name = "openrouter"
    provider.create_model.return_value = "CONCRETE-MODEL"
    return provider


def _registry(*models):
    reg = ModelRegistry()
    reg.register_provider(_provider())
    for m in models:
        reg.register_model(m)
    return reg


def _model(name, priority=100, ctx_window=128000):
    return ModelConfig(
        name=name, provider="openrouter", model_id=f"org/{name}",
        capabilities=("reasoning", "tool_calling", "structured_output"),
        context_window=ctx_window,
        input_cost_per_1m_tokens=0.5,
        output_cost_per_1m_tokens=1.5,
        priority=priority,
    )


@pytest.fixture()
def router():
    return ModelRouter(
        registry=_registry(
            _model("alpha", priority=1),
            _model("beta", priority=2),
            _model("stage-research", priority=3),
        ),
        health=ProviderHealthRegistry(),
    )


def test_route_returns_full_decision(router):
    request = RoutingRequest(task_type=TaskType.SUPPORT, context_tokens=4000,
                             estimated_output_tokens=500)
    decision = router.route(request)

    assert decision.selected_model in {"alpha", "beta"}
    assert decision.provider == "openrouter"
    assert decision.task_type == request.task_type.value
    assert 0.0 <= decision.score <= 1.0
    assert decision.candidates_considered == 3
    assert len(decision.ranked) == 3
    assert decision.reason_codes  # debugging info present (reference §27)
    assert decision.rejected is not None


def test_route_tie_breaks_by_static_priority(router):
    decision = router.route(
        RoutingRequest(task_type=TaskType.SUPPORT, context_tokens=1000)
    )
    # Cold stats => equal scores; ascending model priority wins (reference §26)
    assert decision.selected_model == "alpha"


def test_route_raises_no_candidate_when_everything_filtered():
    router = ModelRouter(registry=_registry(_model("a")), health=ProviderHealthRegistry())
    router._enabled_providers = set()  # nothing enabled
    with pytest.raises(NoCandidateError):
        router.route(RoutingRequest(task_type=TaskType.SUPPORT, context_tokens=10))


def test_role_capabilities_flow_through_route(router):
    caps = {"reasoning", "tool_calling"}
    request = RoutingRequest(task_type=ROLE_TO_TASK_TYPE["documentation_engineer"],
                             context_tokens=2000)
    decision = router.route(request, required_caps=caps)
    assert decision.profile == "documentation_generation"


def test_legacy_resolve_signature_and_return_unchanged(router):
    """resolve(RoutingPolicy) -> Model must keep working (dependencies.py)."""
    policy = RoutingPolicy(required_capabilities=("reasoning",), allow_fallback=True)
    result = router.resolve(policy)
    assert result == "CONCRETE-MODEL"  # provider.create_model output


def test_resolve_model_and_capability_still_work(router):
    assert router.resolve_model("stage-research") == "CONCRETE-MODEL"
