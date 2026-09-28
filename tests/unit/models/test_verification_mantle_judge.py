"""The documentation judge must be a mantle model, and must not be alone.

Context: with the production provider allowlist
(``DRAFTLY_ENABLED_PROVIDERS=mantle,mantle-openai,orcarouter,nvidia,openrouter``)
``TaskType.DOCUMENTATION_REVIEW`` resolved to a *single* candidate,
``review-orca``. Every other model carrying the ``verification`` capability --
``review-model`` and ``stage-review`` on requesty, ``nemotron-ultra-doc`` on
nebius_token_factory -- is filtered out by that allowlist. So one orcarouter
outage took the judge with it, and there was nowhere to fail over to.

The mantle kimi model is registered so the judging path has a second candidate.
The environment below is pinned to the production allowlist on purpose: a test
that builds the router with every provider enabled would not have caught the
single-candidate path at all.
"""

from __future__ import annotations

import pytest

from draftly.models.factory import build_model_router
from draftly.models.router import TASK_TYPE_CAPABILITIES
from draftly.models.schemas import RoutingRequest, TaskType

#: Mirrors ``DRAFTLY_ENABLED_PROVIDERS`` in .env. Neighbouring tests in this
#: package use ``build_model_router(enabled_providers=...)`` for the same reason.
PRODUCTION_PROVIDERS = frozenset(
    {"mantle", "mantle-openai", "orcarouter", "nvidia", "openrouter"}
)

JUDGE = "verification-mantle-kimi-k2-5"


@pytest.fixture
def router(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANTLE_API_KEY", "test-key")
    monkeypatch.setenv("ORCA_API_KEY", "test-key")
    monkeypatch.delenv("DRAFTLY_ENABLED_PROVIDERS", raising=False)
    return build_model_router(enabled_providers=set(PRODUCTION_PROVIDERS))


def _review_request() -> RoutingRequest:
    return RoutingRequest(
        task_type=TaskType.DOCUMENTATION_REVIEW,
        context_tokens=8000,
        estimated_output_tokens=1000,
    )


def test_documentation_review_selects_the_mantle_judge(router) -> None:
    """The in-graph rubric judge is resolved via role ``documentation_reviewer``,
    whose capability floor is ``{"verification"}``."""
    decision = router.route(_review_request())

    assert decision.selected_model == JUDGE


def test_judge_meets_the_documentation_review_capability_floor(router) -> None:
    required = TASK_TYPE_CAPABILITIES[TaskType.DOCUMENTATION_REVIEW]

    config = router.registry.get_model(JUDGE)

    assert required <= set(config.capabilities)


def test_judge_declares_tool_calling(router) -> None:
    """ROLE_POLICIES requires ``("verification", "tool_calling")`` for
    ``documentation_reviewer``; a judge without it cannot drive the agent
    loop even though it satisfies the task-type floor."""
    config = router.registry.get_model(JUDGE)

    assert "tool_calling" in config.capabilities


def test_documentation_review_has_more_than_one_candidate(router) -> None:
    """The regression this change exists to prevent: the judging path resolving
    to a single model, so an outage of that one provider leaves no judge."""
    decision = router.route(_review_request())

    assert len(decision.ranked) > 1, decision.ranked


def test_judge_is_priced_so_it_wins_on_cost_not_just_priority(router) -> None:
    """Priority is only the final tie-breaker (scoring.py sorts by weighted
    score, then priority). The unpriced fallbacks sit at a flat score, so the
    mantle judge has to carry real pricing to outrank them on merit."""
    config = router.registry.get_model(JUDGE)

    assert config.input_cost_per_1m_tokens is not None
    assert config.output_cost_per_1m_tokens is not None
    assert config.context_window is not None
