"""The burst guard must be attached to the writer agent.

The guard is correct in isolation; the defect only closes if the writer's
intervention list actually includes it. Strands only wires a lifecycle hook
when a handler overrides that method at the class level, so the guard must be
a real ``InterventionHandler`` in the agent's registry.
"""

from __future__ import annotations

from draftly.agents.documentation import build_writer_agent
from draftly.steering.tool_call_burst_guard import ToolCallBurstGuard
from tests.stub_model import StubModel


def _writer_intervention_names() -> set[str]:
    agent = build_writer_agent(StubModel(), [])
    registry = agent._intervention_registry
    return {handler.name for handler in registry.handlers}


def test_writer_agent_carries_the_burst_guard() -> None:
    assert ToolCallBurstGuard.name in _writer_intervention_names()


def test_writer_agent_still_carries_the_registry_guard() -> None:
    """The two guards own different failure modes and must both be present."""
    from draftly.steering.tool_registry_guard import ToolRegistryGuard

    names = _writer_intervention_names()
    assert ToolRegistryGuard.name in names
    assert ToolCallBurstGuard.name in names
