"""The context node's turn budget must be wired into the built graph.

Two seams make this easy to get wrong, so both are asserted here:

1. Strands' ``Graph`` calls ``executor.stream_async(input,
   invocation_state=...)`` and forwards no ``limits``. A ``context_limits``
   kwarg on the builder is therefore *not* enough -- the context node has to be
   wrapped in ``LimitedNode`` for the budget to reach the agent.
2. ``build_graph_for_run`` strips documentation-only kwargs for other surfaces.
   Without a matching ``pop``, every non-``pull_request`` surface raises
   ``TypeError`` on the unexpected kwarg.
"""

from __future__ import annotations

from types import SimpleNamespace

from draftly.app.config import Settings
from draftly.agents.shared.turn_budget import LimitedNode
from draftly.integrations.strands.graph import build_graph_for_run

CONTEXT_LIMITS = {"turns": 7}


def _wrapper_stack(executor) -> list[LimitedNode]:
    """Every ``LimitedNode`` in the wrapper chain, outermost first."""
    found: list[LimitedNode] = []
    node = executor
    for _ in range(5):
        if isinstance(node, LimitedNode):
            found.append(node)
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

    executor = graph.nodes["context"].executor
    assert _wrapper_stack(executor)[0].limits == CONTEXT_LIMITS


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

    bounded = {node_id for node_id, node in graph.nodes.items() if _wrapper_stack(node.executor)}

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

    wrappers = _wrapper_stack(graph.nodes["context"].executor)
    assert wrappers, "context node is unbounded when no budget is passed"
    # The exact configured cap, not merely "some truthy cap": a 1-turn default
    # would stop the agent before it read anything and the suite would stay
    # green.
    assert wrappers[0].limits == {
        "turns": Settings().strands.context_max_turns,
    }
    assert wrappers[0].limits["turns"] > 1


def test_the_memory_grounded_path_keeps_the_budget(model, tools, tmp_sessions) -> None:
    """With memory on, the node is wrapped twice; the budget must survive.

    Losing the cap on the memory path would mean the fix works only when
    organizational memory is disabled.
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

    wrappers = _wrapper_stack(graph.nodes["context"].executor)

    assert [w.limits for w in wrappers] == [CONTEXT_LIMITS]


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


