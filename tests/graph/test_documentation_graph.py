"""Documentation graph end-to-end with StubModel (plan §6.9 #1)."""

from __future__ import annotations

from strands.multiagent.base import Status

from draftly.integrations.strands.graph import build_graph_for_run
from tests.graph.conftest import PR_TASK


async def test_full_pipeline_with_revise_loop(model, tools, tmp_sessions) -> None:
    """update → evaluate(FAIL) → update → evaluate(PASS) → deliver."""
    graph = build_graph_for_run(
        "e2e-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "e2e-1", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert order[0] == "classify"
    assert order[-1] == "deliver"
    # revise loop: update and evaluate each ran twice, in sequence
    assert order.count("update") == 2
    assert order.count("evaluate") == 2
    assert order[4:] == [
        "update",
        "evaluate",
        "update",
        "evaluate",
        "deliver",
    ]
    # the wrong generation paths never ran
    assert "answer" not in order
    assert "create" not in order


async def test_invalid_surface_stops_after_classify(model, tools, tmp_sessions) -> None:
    """The is_valid_surface guard blocks unknown event types."""
    graph = build_graph_for_run(
        "e2e-2",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )

    bad_task = '{"event_type": "wiki.deleted", "event_id": "x"}'
    result = await graph.invoke_async(
        bad_task,
        invocation_state={"run_id": "e2e-2", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert order == ["classify"]


async def test_impact_none_skips_generation_and_delivery(model, tools, tmp_sessions) -> None:
    """action='none' fans out to no generation node; graph completes."""
    from draftly.agents.schemas import ImpactAnalysis

    model._structured_outputs[ImpactAnalysis] = {
        "action": "none",
        "affected_documents": [],
        "rationale": "docs already correct",
    }

    graph = build_graph_for_run(
        "e2e-3",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "e2e-3", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert "impact" in order
    for node in ("answer", "update", "create", "evaluate", "deliver"):
        assert node not in order


async def test_graph_builds_with_distinct_writer_instances(model, tools, tmp_sessions) -> None:
    """Duplicate executors are rejected by the SDK — writers must differ."""
    graph = build_graph_for_run(
        "e2e-4",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    assert graph.nodes["update"].executor is not graph.nodes["create"].executor


def test_writer_tools_exclude_mutation_and_delivery(tools) -> None:
    """The doc writer authoring node must NOT expose write/git-delivery tools;
    it produces a DocChangePlan and delivery applies it. Exposing them made the
    writer thrash the worktree (write_file loop) and burn the live-run budget."""
    from draftly.orchestration.graphs.documentation_graph import _scope_writer_tools

    scoped = _scope_writer_tools(tools.documentation_engineer, tools.documentation)
    names = {
        str(
            getattr(t, "name", None)
            or getattr(getattr(t, "fn", None), "__name__", None)
            or getattr(t, "__name__", None)
        )
        for t in scoped
    }
    forbidden = {
        "write_file",
        "update_frontmatter",
        "create_branch",
        "create_commit",
        "create_pull_request",
    }
    assert not (names & forbidden), (
        f"writer must not get mutation/delivery tools: {names & forbidden}"
    )
    # it still keeps read + git-inspect tools it needs to author accurately
    assert {"read_file", "git_diff", "git_status"} <= names
