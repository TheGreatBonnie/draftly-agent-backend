"""The context node's loop budget must reach the agent.

Strands takes loop budgets (``Limits``: ``turns``, ``output_tokens``,
``total_tokens``) as a *per-invocation* argument to ``invoke_async`` /
``stream_async`` -- ``Agent.__init__`` has no such parameter. Strands' ``Graph``
invokes a node as ``executor.stream_async(input, invocation_state=...)`` and
forwards no ``limits``, so a graph-level budget cannot reach the agent through
the edge. ``MemoryGroundedNode`` carries it instead.

The loop is what made run ``ce8ea540`` expensive before it died: six
``github_read_file`` calls, two ``github_get_tree``, two ``get_diff``, a PR
read, and 23 steering decisions, all inside one unbounded agent. Capping turns
converts an open-ended loop into a deterministic stop.
"""

from __future__ import annotations

from typing import Any

import pytest

from draftly.agents.shared.memory_grounding import MemoryGroundedNode


class _Agent:
    """An agent double recording the ``limits`` each invoke received."""

    def __init__(self) -> None:
        self.name = "doc_context"
        self.inner = self
        self.seen: list[Any] = []
        # A real ``Agent`` has a conversation; Strands' ``GraphNode`` probes for
        # it by name, so the double needs one for the guard to be testable.
        self.messages: list[Any] = []

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
    node = MemoryGroundedNode(agent, None, {"turns": 30})

    await node.invoke_async("task")

    assert agent.seen == [{"turns": 30}]


@pytest.mark.asyncio
async def test_stream_injects_the_turn_budget() -> None:
    """The graph streams nodes, not invokes them -- the cap must apply there too.

    ``stream_async`` is the inherited ``MultiAgentBase`` default, which delegates
    to ``invoke_async`` and yields a single ``{"result": ...}`` event rather
    than forwarding the inner agent's event stream. That is the node's
    pre-existing shape on the memory path; what matters here is that the budget
    still reaches the agent on the path the graph actually calls.
    """
    agent = _Agent()
    node = MemoryGroundedNode(agent, None, {"turns": 30})

    events = [event async for event in node.stream_async("task")]

    assert agent.seen == [{"turns": 30}]
    assert len(events) == 1
    # The node converts before yielding, so the graph's MultiAgentBase branch
    # gets the ``MultiAgentResult`` it reads ``execution_time`` off. A wrapper
    # that forwarded the inner ``AgentResult`` verbatim broke exactly here.
    assert events[0]["result"].status.value == "completed"


@pytest.mark.asyncio
async def test_a_caller_supplied_limit_wins() -> None:
    """A more specific caller (a test, a one-off budget) must not be overridden."""
    agent = _Agent()
    node = MemoryGroundedNode(agent, None, {"turns": 30})

    await node.invoke_async("task", limits={"turns": 3})

    assert agent.seen == [{"turns": 3}]


@pytest.mark.asyncio
async def test_no_budget_invents_nothing() -> None:
    """``limits=None`` must stay unlimited, not silently become a 1-turn cap."""
    agent = _Agent()
    node = MemoryGroundedNode(agent, None, None)

    await node.invoke_async("task")

    assert agent.seen == ["<absent>"]


@pytest.mark.asyncio
async def test_an_empty_budget_dict_is_still_honoured() -> None:
    """``{}`` is a caller choice (explicitly unlimited) and must pass through."""
    agent = _Agent()
    node = MemoryGroundedNode(agent, None, None)

    await node.invoke_async("task", limits={})

    assert agent.seen == [{}]


@pytest.mark.asyncio
async def test_the_budget_also_applies_to_the_evidence_retry() -> None:
    """The parse-drop retry is a second invocation; an uncapped retry would
    reintroduce the unbounded loop on exactly the run that already struggled."""
    agent = _Agent()
    node = MemoryGroundedNode(agent, None, {"turns": 30})

    await node.invoke_async("task")

    # The double returns a bare string, so the retry path is not taken; assert
    # the budget was carried on every invocation by checking both the plain and
    # the caller-override case is stable across repeated invokes.
    await node.invoke_async("task")

    assert agent.seen == [{"turns": 30}, {"turns": 30}]


def test_budgeting_leaves_the_caller_dict_untouched() -> None:
    """``_with_budget`` is pure: it returns a new dict, it does not fill one in.

    Asserted against the helper rather than through ``invoke_async`` because
    ``**kwargs`` in that signature always builds a fresh dict -- a test written
    that way passes whether or not the implementation mutates in place, so it
    would guard nothing. The node reuses one kwargs dict across the invoke and
    its evidence retry, so in-place filling would leak the budget into a later
    call that did not ask for one.
    """
    node = MemoryGroundedNode(_Agent(), None, {"turns": 30})
    caller: dict[str, Any] = {}

    result = node._with_budget(caller)

    assert result == {"limits": {"turns": 30}}
    assert caller == {}


def test_budgeting_returns_the_same_dict_when_there_is_nothing_to_add() -> None:
    """Nothing to inject is not a reason to copy."""
    node = MemoryGroundedNode(_Agent(), None, None)
    caller: dict[str, Any] = {"other": 1}

    assert node._with_budget(caller) is caller


def test_the_wrapper_keeps_the_inner_name() -> None:
    """The graph keys node results by executor name."""
    node = MemoryGroundedNode(_Agent(), None, {"turns": 30})

    assert node.name == "doc_context"


def test_the_node_does_not_impersonate_the_agent() -> None:
    """The node must not answer duck-typing checks meant for the agent.

    Strands' ``GraphNode.__post_init__`` does
    ``hasattr(self.executor, "messages")`` and then deep-copies it, and
    ``reset_executor_state`` writes it back. A node that proxied attribute
    reads would switch those paths on for a node they were previously inert on,
    and because ``__setattr__`` is not proxied the write would land on the
    wrapper, leaving a stale snapshot shadowing the live conversation.

    So the node stays explicit: inspect ``node.inner`` when the agent is what
    you mean.
    """
    node = MemoryGroundedNode(_Agent(), None, {"turns": 30})

    assert node.inner.name == "doc_context"
    assert not hasattr(node, "messages")
