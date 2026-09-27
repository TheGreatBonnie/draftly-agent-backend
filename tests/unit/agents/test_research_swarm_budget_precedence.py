"""Research swarm budgets: a plan may tighten the operator budget, never loosen it.

``STRANDS_NODE_TIMEOUT``/``STRANDS_EXECUTION_TIMEOUT`` are deployment-wide
(5400/3600 in the real-PR compose file) while ``ResearchPlan`` carries
per-run research budgets (180/360). Letting the deployment value win gave a
stuck researcher 90 minutes of rope before its node was killed. The plan is
the tighter, more specific budget, so it must win — but a plan that is more
generous than the operator setting must not extend it either.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from draftly.agents.documentation.research_swarm import _swarm_for_plan
from draftly.app.composition.tools import build_tools
from draftly.steering.context import SteeringRuntime
from tests.stub_model import StubModel


def _swarm(*, plan_node_timeout: float, plan_execution_timeout: float, **overrides):
    plan = SimpleNamespace(
        researchers=["docs"],
        max_handoffs=8,
        max_iterations=6,
        node_timeout=plan_node_timeout,
        execution_timeout=plan_execution_timeout,
    )
    tools = build_tools()
    return _swarm_for_plan(
        StubModel(),
        tools,
        plan=plan,
        local_tools=[],
        repo_dir=None,
        github_tools=[],
        runtime=SteeringRuntime.disabled(),
        node_id="research",
        **overrides,
    )


@pytest.mark.parametrize("field", ["node_timeout", "execution_timeout"])
def test_plan_tightens_a_more_generous_operator_budget(field: str) -> None:
    """A plan's tight research budget must not be widened by the deployment value."""
    swarm = _swarm(
        plan_node_timeout=180.0,
        plan_execution_timeout=360.0,
        node_timeout=5400.0,
        execution_timeout=3600.0,
    )

    assert getattr(swarm, field) == pytest.approx(180.0 if field == "node_timeout" else 360.0)


@pytest.mark.parametrize("field", ["node_timeout", "execution_timeout"])
def test_plan_does_not_loosen_a_tighter_operator_budget(field: str) -> None:
    """A plan more generous than the operator setting must not extend it."""
    swarm = _swarm(
        plan_node_timeout=9000.0,
        plan_execution_timeout=9000.0,
        node_timeout=5400.0,
        execution_timeout=3600.0,
    )

    assert getattr(swarm, field) == pytest.approx(5400.0 if field == "node_timeout" else 3600.0)


def test_plan_budget_applies_when_no_operator_override_is_passed() -> None:
    swarm = _swarm(plan_node_timeout=180.0, plan_execution_timeout=360.0)

    assert swarm.node_timeout == pytest.approx(180.0)
    assert swarm.execution_timeout == pytest.approx(360.0)
