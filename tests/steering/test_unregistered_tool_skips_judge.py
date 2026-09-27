"""An unregistered tool name must not cost a policy evaluation.

Observed in run ``84a92a60``: the writer agent asked for ``github_get_file``
and ``read_file`` when the registry only has ``github_read_file``. Strands
schedules the two checks in *different* hook systems, so order is fixed and
cannot be changed from our side:

  * the steering plugin's ``provide_tool_steering_guidance`` -- plain ``@hook``
    -- runs at ``HookOrder.DEFAULT`` (0);
  * ``InterventionRegistry`` -- which owns ``ToolRegistryGuard`` -- registers
    ``BeforeToolCallEvent`` at ``HookOrder.INTERVENTION_INPUT`` (90).

Lower runs first, so the policy is *always* evaluated before the guard rejects
the name. Reordering the ``interventions`` list in ``agents/factory.py`` cannot
change this, because the whole registry fires at order 90 regardless of
position within the list.

The only correct place to stop the wasted call is the steering handler itself:
if the agent's registry does not contain the name, the call cannot execute no
matter what the policy says, so evaluating the policy is pure waste. That run
spent 18 policy evaluations that reached the LLM judge (12 on
``github_get_file``, 6 on ``read_file``) answering "proceed" for calls that
were structurally impossible.

The boundary asserted here is ``policy.evaluate_tool_async`` -- the single
entry point that may invoke the judge -- rather than the judge itself, because
the judge is reached only conditionally from inside it.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from draftly.steering.decisions import AgentRole
from draftly.steering.handler import DraftlySteeringHandler
from draftly.steering.policy import policy_for

from .test_handler import CHECKOUT, build_runtime

#: Exactly the registry the writer agent had in the failing run.
WRITER_TOOLS = frozenset(
    {
        "DocChangePlan",
        "analyze_structure",
        "append_chunk",
        "extract_frontmatter",
        "extract_links",
        "finalize_draft",
        "find_section",
        "generate_toc",
        "github_get_tree",
        "github_read_file",
        "markdown_to_text",
        "skills",
        "split_sections",
        "start_draft",
        "validate_links",
    }
)

#: The two names the model actually asked for; neither is registered.
HALLUCINATED = ("github_get_file", "read_file")


class AgentWithRegistry:
    """A strands-like agent that can enumerate its tools."""

    def __init__(self, tool_names) -> None:
        self.tool_names = tool_names


class AgentWithoutRegistry:
    """Duck-typed agent that cannot enumerate tools (the pre-existing test shape)."""


class CountingPolicy:
    """Delegates to the real policy while counting consultations."""

    def __init__(self, policy) -> None:
        self._inner = policy
        self.tool_calls = 0
        self.model_calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def evaluate_tool_async(self, **kwargs):
        self.tool_calls += 1
        return await self._inner.evaluate_tool_async(**kwargs)

    async def evaluate_tool_shadow(self, **kwargs):
        self.tool_calls += 1
        return await self._inner.evaluate_tool_shadow(**kwargs)

    async def evaluate_model_async(self, **kwargs):
        self.model_calls += 1
        return await self._inner.evaluate_model_async(**kwargs)

    async def evaluate_model_shadow(self, **kwargs):
        self.model_calls += 1
        return await self._inner.evaluate_model_shadow(**kwargs)


@pytest.fixture
def runtime():
    return build_runtime(role=AgentRole.DELIVERY)


@pytest.fixture
def counted() -> CountingPolicy:
    return CountingPolicy(policy_for(AgentRole.DELIVERY))


@pytest.mark.parametrize("tool_name", HALLUCINATED)
async def test_unregistered_tool_never_consults_the_policy(runtime, counted, tool_name):
    """The regression: 18 wasted LLM round-trips in a single run."""
    handler = DraftlySteeringHandler(runtime=runtime, policy=counted)

    action = await handler.steer_before_tool(
        agent=AgentWithRegistry(WRITER_TOOLS),
        tool_use={"name": tool_name, "path": "src/authly/oauth.py"},
    )

    assert counted.tool_calls == 0
    assert type(action).__name__ == "Proceed"


async def test_registered_tool_still_consults_the_policy(runtime, counted):
    """Control: the skip must not disarm steering for real tools."""
    handler = DraftlySteeringHandler(runtime=runtime, policy=counted)

    action = await handler.steer_before_tool(
        agent=AgentWithRegistry(WRITER_TOOLS),
        tool_use={"name": "github_read_file", "path": f"{CHECKOUT}/docs/index.md"},
    )

    assert counted.tool_calls == 1
    assert type(action).__name__ in {"Proceed", "Guide", "Deny"}


async def test_agent_without_tool_names_keeps_previous_behaviour(runtime, counted):
    """An agent that cannot enumerate tools must not be silently waved through."""
    handler = DraftlySteeringHandler(runtime=runtime, policy=counted)

    action = await handler.steer_before_tool(
        agent=AgentWithoutRegistry(),
        tool_use={"name": "github_read_file", "path": f"{CHECKOUT}/docs/index.md"},
    )

    assert counted.tool_calls == 1
    assert type(action).__name__ in {"Proceed", "Guide", "Deny"}


async def test_skip_does_not_audit_a_decision_it_never_made(runtime, counted):
    """Returning early must not fabricate an audit record for a non-decision."""
    handler = DraftlySteeringHandler(runtime=runtime, policy=counted)

    await handler.steer_before_tool(
        agent=AgentWithRegistry(WRITER_TOOLS),
        tool_use={"name": "github_get_file", "path": "src/authly/oauth.py"},
    )

    runtime.audit.record_step.assert_not_awaited()


async def test_registry_guard_still_owns_the_rejection():
    """The steering skip must not swallow the guard's correction.

    The guard is a separate hook at order 90 and remains the component that
    actually tells the model the name is wrong and ultimately fails the node.
    """
    from draftly.steering.tool_registry_guard import ToolRegistryGuard

    guard = ToolRegistryGuard(max_guides=2)
    event = Mock()
    event.tool_use = {"name": "github_get_file"}
    event.agent.tool_names = WRITER_TOOLS

    assert type(guard.before_tool_call(event)).__name__ == "Guide"
    assert type(guard.before_tool_call(event)).__name__ == "Guide"

    with pytest.raises(RuntimeError, match="Repeated unregistered tool"):
        guard.before_tool_call(event)
