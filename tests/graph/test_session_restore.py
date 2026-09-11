"""Session persistence across graph rebuilds (plan §6.9 #5).

A fresh graph built with the same session_id + storage_dir restores the
interrupted execution state on construction; resuming then completes the
run. One run = one session = one graph.
"""

from __future__ import annotations

from pathlib import Path

from strands.multiagent.base import Status

from draftly.integrations.strands.graph import (
    build_graph_for_run,
    build_session_manager,
)
from tests.graph.conftest import PR_TASK


async def test_interrupted_state_restores_on_rebuild(
    model, tools, tmp_sessions, comment_factory
) -> None:
    run_id = "sess-1"
    factory, _ = comment_factory

    first = build_graph_for_run(
        run_id,
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
    )
    result = await first.invoke_async(
        PR_TASK,
        invocation_state={"run_id": run_id, "review_policy": "always"},
    )
    assert result.status == Status.INTERRUPTED
    interrupt_id = result.interrupts[0].id

    # Session files were persisted under the storage dir
    assert (Path(tmp_sessions) / f"session_draftly-{run_id}").exists()

    # A NEW graph instance with the SAME session restores interrupted state
    second = build_graph_for_run(
        run_id,
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
    )
    resumed = await second.invoke_async(
        [
            {
                "interruptResponse": {
                    "interruptId": interrupt_id,
                    "response": {"approved": True},
                }
            }
        ],
        invocation_state={"run_id": run_id, "review_policy": "always"},
    )

    assert resumed.status == Status.COMPLETED
    assert [n.node_id for n in resumed.execution_order][-1] == "deliver"


async def test_resume_does_not_repost_notify_comment(
    model, tools, tmp_sessions, comment_factory
) -> None:
    """A resumed run must not re-post the PR notify comment: strands resume
    skips completed nodes, so notify_post fires exactly once across the
    interrupted first pass and the resumed completion."""
    run_id = "sess-3"
    factory, commenter = comment_factory

    first = build_graph_for_run(
        run_id,
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
    )
    result = await first.invoke_async(
        PR_TASK,
        invocation_state={"run_id": run_id, "review_policy": "always"},
    )
    assert result.status == Status.INTERRUPTED
    assert "notify_post" in [n.node_id for n in result.execution_order]
    assert len(commenter.calls) == 1

    interrupt_id = result.interrupts[0].id
    second = build_graph_for_run(
        run_id,
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
    )
    resumed = await second.invoke_async(
        [
            {
                "interruptResponse": {
                    "interruptId": interrupt_id,
                    "response": {"approved": True},
                }
            }
        ],
        invocation_state={"run_id": run_id, "review_policy": "always"},
    )

    assert resumed.status == Status.COMPLETED
    assert len(commenter.calls) == 1


async def test_resume_requires_interrupt_response_format(
    model, tools, tmp_sessions, comment_factory
) -> None:
    """Resuming an activated interrupt with a plain string is a TypeError."""
    run_id = "sess-2"
    factory, _ = comment_factory

    first = build_graph_for_run(
        run_id,
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
    )
    result = await first.invoke_async(
        PR_TASK,
        invocation_state={"run_id": run_id, "review_policy": "always"},
    )
    assert result.status == Status.INTERRUPTED

    second = build_graph_for_run(
        run_id,
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
    )
    import pytest

    with pytest.raises(TypeError):
        await second.invoke_async(
            "plain string resume",
            invocation_state={"run_id": run_id},
        )


def test_session_manager_is_per_run(tmp_sessions) -> None:
    """Two runs get distinct session ids in the same storage dir."""
    a = build_session_manager("run-a", tmp_sessions)
    b = build_session_manager("run-b", tmp_sessions)
    assert a.session_id != b.session_id
