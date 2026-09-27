"""`research` must survive the loss of its primary provider.

Regression: the only ``research``-capable model was
``nemotron-super-research`` on ``nebius_token_factory``. When that provider
was filtered out, ``route()`` raised ``NoCandidateError`` -- so a timeout on
the primary had nowhere to fail over to, and every failover attempt for the
``research`` task type died before it started.

The mantle models below were probed on the exact failing path (non-streaming
``parse()`` with a ``response_format``): both finish with ``stop`` and
non-zero reasoning, well inside the 60s provider timeout.
"""

from __future__ import annotations

import pytest

from draftly.models.factory import build_model_router
from draftly.models.router import TASK_TYPE_CAPABILITIES
from draftly.models.schemas import RoutingRequest, TaskType

FALLBACK_MODELS = (
    "research-mantle-qwen3-coder-next",
    "research-mantle-minimax-m2",
)


@pytest.fixture
def router(monkeypatch: pytest.MonkeyPatch):
    for var in ("MANTLE_API_KEY", "NEBIUS_TOKEN_FACTORY_API_KEY"):
        monkeypatch.setenv(var, "test-key")
    monkeypatch.delenv("DRAFTLY_ENABLED_PROVIDERS", raising=False)
    return build_model_router(enabled_providers={"mantle", "nebius_token_factory"})


def test_research_has_a_fallback_when_primary_provider_is_lost(router) -> None:
    """The NoCandidateError regression: research must route somewhere else."""
    router.health.get("nebius_token_factory").disable()

    decision = router.route(RoutingRequest(task_type=TaskType.RESEARCH, context_tokens=8192))

    assert decision.provider != "nebius_token_factory"
    assert decision.provider == "mantle"


def test_every_fallback_model_declares_the_research_capability(router) -> None:
    """TaskType.RESEARCH's capability floor is {"research"}; the floor must be met."""
    required = TASK_TYPE_CAPABILITIES[TaskType.RESEARCH]

    for name in FALLBACK_MODELS:
        config = router.registry.get_model(name)
        assert required <= set(config.capabilities), name


def test_fallback_models_support_structured_output(router) -> None:
    """context/research agents emit Pydantic output via a non-streaming parse()."""
    for name in FALLBACK_MODELS:
        config = router.registry.get_model(name)
        assert "structured_output" in config.capabilities, name


def test_fallback_prefers_the_faster_model_when_scores_tie(router) -> None:
    """Priority is only the final tie-breaker, so it must still be deterministic."""
    qwen = router.registry.get_model("research-mantle-qwen3-coder-next")
    minimax = router.registry.get_model("research-mantle-minimax-m2")

    assert qwen.priority < minimax.priority


def test_research_still_prefers_the_primary_provider_when_healthy(router) -> None:
    """Adding fallbacks must not silently demote nemotron-super-research."""
    decision = router.route(RoutingRequest(task_type=TaskType.RESEARCH, context_tokens=8192))

    assert decision.selected_model == "nemotron-super-research"
