"""Gate + determinism tests for the Nebius Token Factory integration.

With the provider gated on via ``enabled_providers={"nebius_token_factory"}``
every agent role must route to ``nebius_token_factory`` and resolve to a
deterministic Nemotron tier — no stats, so cost scoring decides among the
capability-eligible models:

  SUPPORT / FAST / DELIVERY      -> nemotron-nano-fast   (cheapest, tool_calling)
  RESEARCH / EVALUATION          -> nemotron-super-research
  DOCGEN / DOCREVIEW / REASONING -> nemotron-ultra-doc   (only eligible tier)
"""

from __future__ import annotations

import pytest

from draftly.models.factory import build_model_router
from draftly.models.schemas import ROLE_TO_TASK_TYPE, RoutingRequest, TaskType

EXPECTED_TIER: dict[TaskType, str] = {
    TaskType.SUPPORT: "nemotron-nano-fast",
    TaskType.FAST: "nemotron-nano-fast",
    TaskType.DELIVERY: "nemotron-nano-fast",
    TaskType.REASONING: "nemotron-nano-fast",
    TaskType.RESEARCH: "nemotron-super-research",
    TaskType.EVALUATION: "nemotron-super-research",
    TaskType.DOCUMENTATION_GENERATION: "nemotron-ultra-doc",
    TaskType.DOCUMENTATION_REVIEW: "nemotron-ultra-doc",
}


def _tf_router():
    return build_model_router(enabled_providers={"nebius_token_factory"})


def _route(task_type: TaskType):
    decision = _tf_router().route(RoutingRequest(task_type=task_type, context_tokens=1000))
    return decision


class TestTokenFactoryGate:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEBIUS_TOKEN_FACTORY_API_KEY", "test-key")

    @pytest.mark.parametrize("task_type", list(TaskType))
    def test_every_task_type_routes_to_token_factory(self, task_type: TaskType) -> None:
        decision = _route(task_type)

        assert decision.provider == "nebius_token_factory"

    @pytest.mark.parametrize("task_type, expected", EXPECTED_TIER.items())
    def test_deterministic_tier_per_task_type(self, task_type: TaskType, expected: str) -> None:
        decision = _route(task_type)

        assert decision.selected_model == expected

    @pytest.mark.parametrize("role, task_type", ROLE_TO_TASK_TYPE.items())
    def test_every_role_gates_to_token_factory(self, role: str, task_type: TaskType) -> None:
        decision = _route(task_type)

        assert decision.provider == "nebius_token_factory"
        assert decision.task_type == task_type.value

    def test_determinism_identical_across_runs(self) -> None:
        first = _route(TaskType.RESEARCH)
        second = _route(TaskType.RESEARCH)

        assert first.selected_model == second.selected_model
        assert first.provider == second.provider


class TestTokenFactoryOff:
    def test_off_gate_never_selects_token_factory(self) -> None:
        router = build_model_router(enabled_providers={"mantle"})
        decision = router.route(RoutingRequest(task_type=TaskType.REASONING, context_tokens=1000))

        assert decision.provider != "nebius_token_factory"


class TestTokenFactoryEmbeddingGate:
    def test_only_tf_key_routes_to_qwen_1536(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in ("REQUESTY_API_KEY", "ORCAROUTER_API_KEY", "OPENROUTER_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("NEBIUS_TOKEN_FACTORY_API_KEY", "test-key")
        monkeypatch.setenv("EMBEDDING_MODEL_ID", "Qwen/Qwen3-Embedding-8B")

        from draftly.models.factory import build_embedding_router

        ranked = build_embedding_router().registry.list_embedding_models()

        assert [m.provider for m in ranked] == ["nebius_token_factory"]
        assert ranked[0].model_id == "Qwen/Qwen3-Embedding-8B"
        assert ranked[0].dimensions == 1536
