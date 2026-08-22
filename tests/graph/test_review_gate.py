"""Review gate: interrupt at delivery, resume with approval or rejection
(plan §6.9 #2–#4).

SDK behavior verified against strands-agents 1.52.0:
- First run halts with ``Status.INTERRUPTED``; the interrupt is reported in
  ``result.interrupts`` with a deterministic id (uuid5 of node+name).
- Resume passes ``[{"interruptResponse": {"interruptId", "response"}}]``.
- Rejection sets ``cancel_node``, which makes ``invoke_async`` RAISE
  RuntimeError (fail-fast) — not return a FAILED result.
"""

from __future__ import annotations

import pytest
from strands.multiagent.base import Status

from draftly.integrations.strands.graph import build_graph_for_run
from tests.graph.conftest import PR_TASK


def _interrupt_id(result) -> str:
    assert result.status == Status.INTERRUPTED
    assert result.interrupts, "expected the review gate to raise an interrupt"
    return result.interrupts[0].id


async def _run_to_interrupt(model, tools, tmp_sessions, run_id: str):
    graph = build_graph_for_run(
        run_id,
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": run_id, "review_policy": "always"},
    )
    return graph, result


async def test_gate_interrupts_before_delivery(model, tools, tmp_sessions) -> None:
    _, result = await _run_to_interrupt(model, tools, tmp_sessions, "gate-1")
    interrupt_id = _interrupt_id(result)

    order = [n.node_id for n in result.execution_order]
    assert "deliver" not in order
    assert "evaluate" in order
    assert interrupt_id.startswith("v1:before_node_call:")


async def test_resume_with_approval_completes_delivery(model, tools, tmp_sessions) -> None:
    _, first = await _run_to_interrupt(model, tools, tmp_sessions, "gate-2")
    interrupt_id = _interrupt_id(first)

    graph = build_graph_for_run(
        "gate-2",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    result = await graph.invoke_async(
        [
            {
                "interruptResponse": {
                    "interruptId": interrupt_id,
                    "response": {"approved": True, "comment": "ship it"},
                }
            }
        ],
        invocation_state={"run_id": "gate-2", "review_policy": "always"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert order[-1] == "deliver"


async def test_rejection_cancels_node_and_raises(model, tools, tmp_sessions) -> None:
    _, first = await _run_to_interrupt(model, tools, tmp_sessions, "gate-3")
    interrupt_id = _interrupt_id(first)

    graph = build_graph_for_run(
        "gate-3",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    with pytest.raises(RuntimeError, match="Rejected by reviewer"):
        await graph.invoke_async(
            [
                {
                    "interruptResponse": {
                        "interruptId": interrupt_id,
                        "response": {"approved": False, "comment": "nope"},
                    }
                }
            ],
            invocation_state={"run_id": "gate-3", "review_policy": "always"},
        )


async def test_policy_never_skips_the_gate(model, tools, tmp_sessions) -> None:
    graph = build_graph_for_run(
        "gate-4",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "gate-4", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    assert [n.node_id for n in result.execution_order][-1] == "deliver"
