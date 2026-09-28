"""The context node's turn budget must be wired into the built graph.

Three seams make this easy to get wrong, so all three are asserted here:

1. Strands' ``Graph`` calls ``executor.stream_async(input,
   invocation_state=...)`` and forwards no ``limits``. A ``context_limits``
   kwarg on the builder is therefore *not* enough -- the node has to be wrapped
   in something that injects the budget per invocation.
2. The carrier has to be ``MemoryGroundedNode``, not an arbitrary
   ``MultiAgentBase``. Wrapping a bare ``Agent`` in a new ``MultiAgentBase``
   changes which branch the graph takes, and the graph then reads
   ``execution_time`` off the forwarded ``AgentResult`` instead of a
   ``MultiAgentResult`` -- 20 graph tests failed this way.
3. ``build_graph_for_run`` strips documentation-only kwargs for other surfaces.
   Without a matching ``pop``, every non-``pull_request`` surface raises
   ``TypeError`` on the unexpected kwarg.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from draftly.agents.shared.memory_grounding import MemoryGroundedNode
from draftly.app.config import Settings
from draftly.integrations.strands.graph import build_graph_for_run

CONTEXT_LIMITS = {"turns": 7}


def _budgets(executor: Any) -> list[dict[str, Any]]:
    """Every budget a node wrapper will inject, outermost first.

    ``None`` (an unlimited wrapper) is skipped, so this answers "which nodes
    are bounded?" rather than "which nodes are wrapped?".
    """
    found: list[dict[str, Any]] = []
    node = executor
    for _ in range(5):
        if isinstance(node, MemoryGroundedNode) and node.limits is not None:
            found.append(node.limits)
        node = getattr(node, "inner", None)
        if node is None:
            break
    return found


def test_the_context_node_carries_the_turn_budget(model, tools, tmp_sessions) -> None:
    graph = build_graph_for_run(
        "context-limits-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        context_limits=CONTEXT_LIMITS,
    )

    assert _budgets(graph.nodes["context"].executor)[0] == CONTEXT_LIMITS


def test_the_budget_wraps_the_context_agent_not_another_node(model, tools, tmp_sessions) -> None:
    """Only the context agent is bounded; the writer keeps its own budget."""
    graph = build_graph_for_run(
        "context-limits-2",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        context_limits=CONTEXT_LIMITS,
    )

    bounded = {node_id for node_id, node in graph.nodes.items() if _budgets(node.executor)}

    assert bounded == {"context"}


def test_a_default_run_still_bounds_the_context_agent(model, tools, tmp_sessions) -> None:
    """No explicit budget configured must not mean 'unbounded'.

    The wiring lives in ``build_graph_for_run``/the builder default, so a graph
    built without the kwarg still has to come out bounded.
    """
    graph = build_graph_for_run(
        "context-limits-3",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )

    budgets = _budgets(graph.nodes["context"].executor)
    assert budgets, "context node is unbounded when no budget is passed"
    # The exact configured cap, not merely "some truthy cap": a 1-turn default
    # would stop the agent before it read anything and the suite would stay
    # green.
    assert budgets[0] == {"turns": Settings().strands.context_max_turns}
    assert budgets[0]["turns"] > 1


def test_the_memory_grounded_path_keeps_the_budget(model, tools, tmp_sessions) -> None:
    """With memory on, the node still gets exactly one budget, not two.

    Losing the cap on the memory path would mean the fix works only when
    organizational memory is disabled; nesting a second carrier would mean the
    memory path double-counts the same limit.
    """
    memory = SimpleNamespace(knowledge=None, episodes=None, procedures=None)
    graph = build_graph_for_run(
        "context-limits-4",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        memory=memory,
        context_limits=CONTEXT_LIMITS,
    )

    assert _budgets(graph.nodes["context"].executor) == [CONTEXT_LIMITS]


def test_the_node_still_satisfies_the_graph_result_contract(model, tools, tmp_sessions) -> None:
    """Regression: the carrier must convert, not forward, the inner result.

    Strands' graph reads ``execution_time`` off whatever a ``MultiAgentBase``
    returns, so a wrapper that forwards the bare ``AgentResult`` fails the whole
    graph at runtime. ``MemoryGroundedNode`` converts, which is why it is the
    carrier.
    """
    graph = build_graph_for_run(
        "context-limits-5",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        context_limits=CONTEXT_LIMITS,
    )

    executor = graph.nodes["context"].executor
    # The conversion itself is asserted in
    # tests/unit/agents/shared/test_context_turn_budget.py; what the graph can
    # see here is only that the executor is the converting wrapper, not an
    # anonymous one that forwards the inner result. ``.inner`` rather than
    # attribute proxying -- see the note in that test.
    assert isinstance(executor, MemoryGroundedNode)
    assert executor.inner.name == "doc_context"


def test_other_surfaces_ignore_the_context_budget(model, tools, tmp_sessions) -> None:
    """``context_limits`` is documentation-only; other builders reject unknown kwargs."""
    for surface in ("issue", "support", "feedback"):
        graph = build_graph_for_run(
            f"context-limits-{surface}",
            surface=surface,
            tools_registry=tools,
            model=model,
            storage_dir=tmp_sessions,
            context_limits=CONTEXT_LIMITS,
        )
        assert graph.nodes
