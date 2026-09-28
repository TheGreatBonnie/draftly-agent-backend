"""The context agent must have a turn budget the graph can actually deliver.

``build_doc_context_agent`` sets no ``limits``, and Strands' ``Graph`` calls
``node.executor.stream_async(input, invocation_state=...)`` -- it never
forwards a ``limits`` kwarg, so a graph-level budget cannot reach the agent.
The writer solves the same problem with a per-invoke kwarg forwarded by its
own handler; the context node has no handler, so the budget has to be injected
by a node wrapper.

The loop is what made run ``ce8ea540`` expensive before it died: six
``github_read_file`` calls, two ``github_get_tree``, two ``get_diff``, a PR
read, and 23 steering decisions, all inside one unbounded agent. Capping turns
converts an open-ended loop into a deterministic stop.
"""

from __future__ import annotations

from typing import Any

import pytest

from draftly.agents.shared.turn_budget import LimitedNode


class _Agent:
    """An agent double recording the ``limits`` each invoke received."""

    def __init__(self) -> None:
        self.name = "doc_context"
        self.inner = self
        self.seen: list[Any] = []

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        self.seen.append(kwargs.get("limits", "<absent>"))
        return "result"

    async def stream_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        self.seen.append(kwargs.get("limits", "<absent>"))
        for index in range(2):
            yield {"index": index}


@pytest.mark.asyncio
async def test_invoke_injects_the_turn_budget() -> None:
    agent = _Agent()
    node = LimitedNode(agent, {"turns": 12})

    await node.invoke_async("task")

    assert agent.seen == [{"turns": 12}]


@pytest.mark.asyncio
async def test_stream_injects_the_turn_budget() -> None:
    """The graph streams nodes, not invokes them -- the cap must apply there too."""
    agent = _Agent()
    node = LimitedNode(agent, {"turns": 12})

    events = [event async for event in node.stream_async("task")]

    assert agent.seen == [{"turns": 12}]
    assert [event["index"] for event in events] == [0, 1]


@pytest.mark.asyncio
async def test_a_caller_supplied_limit_wins() -> None:
    """A more specific caller (a test, a one-off budget) must not be overridden."""
    agent = _Agent()
    node = LimitedNode(agent, {"turns": 12})

    await node.invoke_async("task", limits={"turns": 3})

    assert agent.seen == [{"turns": 3}]


@pytest.mark.asyncio
async def test_no_budget_invents_nothing() -> None:
    """``limits=None`` must stay unlimited, not silently become a 1-turn cap."""
    agent = _Agent()
    node = LimitedNode(agent, None)

    await node.invoke_async("task")

    assert agent.seen == ["<absent>"]


@pytest.mark.asyncio
async def test_an_empty_budget_dict_is_still_honoured() -> None:
    """``{}`` is a caller choice (explicitly unlimited) and must pass through."""
    agent = _Agent()
    node = LimitedNode(agent, None)

    await node.invoke_async("task", limits={})

    assert agent.seen == [{}]


@pytest.mark.asyncio
async def test_the_invoke_result_is_passed_through_untouched() -> None:
    """Wrapping must not reshape the payload.

    The context node's ``structured_output`` (the ``EvidenceBundle``) is what
    the research and evaluation nodes consume; a wrapper that rebuilt the
    result would throw the evidence away.
    """
    agent = _Agent()
    node = LimitedNode(agent, {"turns": 12})

    assert await node.invoke_async("task") == "result"


def test_the_wrapper_keeps_the_inner_name() -> None:
    """The graph keys node results by executor name."""
    node = LimitedNode(_Agent(), {"turns": 12})

    assert node.name == "doc_context"
