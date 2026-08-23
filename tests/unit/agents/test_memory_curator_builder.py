"""Memory curator builder tests."""

from draftly.agents.prompts import MEMORY_CURATOR_PROMPT
from draftly.agents.shared.memory_curator import build_memory_curator
from draftly.tools.memory.search import memory_search


def test_builder_wires_tools():
    agent = build_memory_curator(model=None, tools=[memory_search])
    registered = set(agent.tool_registry.registry.keys())
    assert "memory_search" in registered


def test_prompt_carries_decision_contract():
    for phrase in ("CREATE", "SUPERSEDE", '"decisions"', "memory_search"):
        assert phrase in MEMORY_CURATOR_PROMPT
