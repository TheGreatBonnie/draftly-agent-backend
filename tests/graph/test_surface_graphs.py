"""Issue, support, feedback, and evaluation graphs end-to-end."""

from __future__ import annotations

import pytest
from strands.multiagent.base import Status

from draftly.integrations.strands.graph import build_graph_for_run
from draftly.orchestration.graphs.evaluation_graph import (
    build_evaluation_graph,
)
from draftly.orchestration.graphs.feedback_graph import build_feedback_graph
from draftly.orchestration.hooks.review_gate import ReviewGate
from tests.graph.conftest import ISSUE_TASK, SUPPORT_TASK


async def test_issue_graph_answers_and_delivers(model, tools, tmp_sessions) -> None:
    graph = build_graph_for_run(
        "issue-1",
        surface="issue",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    result = await graph.invoke_async(
        ISSUE_TASK,
        invocation_state={"run_id": "issue-1", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert order[0] == "classify"
    assert order[-1] == "deliver"


def test_issue_and_support_writers_are_read_only(model, tools, tmp_sessions) -> None:
    forbidden = {
        "write_file",
        "update_frontmatter",
        "create_branch",
        "create_commit",
        "create_pull_request",
    }
    for surface, run_id in (("issue", "scope-issue"), ("support", "scope-support")):
        graph = build_graph_for_run(
            run_id,
            surface=surface,
            tools_registry=tools,
            model=model,
            storage_dir=tmp_sessions,
        )
        for node_id in ("update", "create"):
            names = {
                str(
                    getattr(t, "name", None)
                    or getattr(getattr(t, "fn", None), "__name__", None)
                    or getattr(t, "__name__", None)
                )
                for t in graph.nodes[node_id].executor.tool_names
            }
            assert not names & forbidden
async def test_support_graph_runs_through_triage(model, tools, tmp_sessions) -> None:
    graph = build_graph_for_run(
        "support-1",
        surface="support",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    result = await graph.invoke_async(
        SUPPORT_TASK,
        invocation_state={
            "run_id": "support-1",
            "review_policy": "never",
            # support graph has one extra node (triage): relax the cap
            "__ignore__": True,
        },
        max_node_executions=12,
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert "triage" in order
    assert order[-1] == "deliver"


async def test_feedback_graph_detects_and_enqueues_gaps() -> None:
    graph = build_feedback_graph(gap_threshold=2)
    task = (
        '{"questions": ['
        '{"topic": "retries", "question": "how to retry?", "source": "slack"}, '
        '{"topic": "retries", "question": "retry limits?", "source": "discord"}, '
        '{"topic": "auth", "question": "api keys?", "source": "slack"}'
        "]}"
    )
    result = await graph.invoke_async(task)

    assert result.status == Status.COMPLETED
    assert [n.node_id for n in result.execution_order][-1] == "enqueue"

    from draftly.orchestration.nodes.base import node_data

    gaps = node_data(graph.state, "detect_gaps")
    assert gaps["gap_count"] == 1
    assert gaps["gaps"][0]["topic"] == "retries"

    enqueued = node_data(graph.state, "enqueue")
    assert enqueued["enqueued_count"] == 1
    assert enqueued["enqueued"][0]["action"] == "create"


async def test_feedback_graph_skips_enqueue_without_gaps() -> None:
    graph = build_feedback_graph(gap_threshold=5)
    task = '{"questions": [{"topic": "a", "question": "q", "source": "slack"}]}'
    result = await graph.invoke_async(task)

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert "prioritize" in order
    assert "enqueue" not in order


async def test_evaluation_graph_runs_deterministic_experiments() -> None:
    graph = build_evaluation_graph()
    task = "{}"
    datasets = [
        {
            "name": "docs-quality",
            "cases": [
                {
                    "name": "grounded answer",
                    "input": "explain retries",
                    "expected_output": "retry with backoff",
                }
            ],
        }
    ]
    result = await graph.invoke_async(
        task,
        invocation_state={"datasets": datasets},
    )

    assert result.status == Status.COMPLETED

    from draftly.orchestration.nodes.base import node_data

    summary = node_data(graph.state, "persist")
    assert summary["total"] >= 1
    assert summary["passed"] == summary["total"]
    assert summary["passed_all"] is True


async def test_slack_surface_builds_support_graph(model, tools, tmp_sessions) -> None:
    """Slack-sourced cases must route to the support graph, not the
    documentation graph: the support graph has a ``triage`` node and NO
    ``changelog`` node, so a support case never authors a changelog entry."""
    graph = build_graph_for_run(
        "slack-1",
        surface="slack",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )

    assert "triage" in graph.nodes
    assert "changelog" not in graph.nodes


async def test_discord_surface_builds_support_graph(model, tools, tmp_sessions) -> None:
    graph = build_graph_for_run(
        "discord-1",
        surface="discord",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )

    assert "triage" in graph.nodes
    assert "changelog" not in graph.nodes


@pytest.mark.parametrize(
    ("surface", "present", "absent"),
    [
        ("pull_request", ["changelog"], ["triage"]),
        ("release", ["changelog"], ["triage"]),
        ("issue", [], ["changelog", "triage"]),
        ("support", ["triage"], ["changelog"]),
        ("slack", ["triage"], ["changelog"]),
        ("discord", ["triage"], ["changelog"]),
    ],
    ids=lambda value: ",".join(value) if isinstance(value, list) else value,
)
async def test_evaluation_surface_routes_to_expected_graph(
    surface, present, absent, model, tools, tmp_sessions
) -> None:
    """Lock in the evaluation routing table: every evaluation surface must
    build the graph its cases actually claim. Doc surfaces (pull_request,
    release) author a changelog and never run triage; the support surfaces
    (support/slack/discord) run triage and never author a changelog; issue
    runs neither. Mirrors _BUILDERS in draftly.integrations.strands.graph."""
    graph = build_graph_for_run(
        f"route-{surface}",
        surface=surface,
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )

    for node in present:
        assert node in graph.nodes, f"{surface} graph missing {node!r}"
    for node in absent:
        assert node not in graph.nodes, f"{surface} graph should not contain {node!r}"


def test_all_surface_graphs_keep_review_gate_and_steering(graph_builder_fixtures) -> None:
    """Every surface graph keeps the human-in-the-loop ReviewGate and wires
    one run-scoped SteeringRuntime with a stable identity into every agent."""
    for surface, fixture in graph_builder_fixtures.items():
        graph = fixture.build(surface=surface, steering_enabled=True)
        assert fixture.has_provider(graph, ReviewGate), f"{surface} lost ReviewGate"
        assert fixture.application_agents_have_steering(graph), f"{surface} steering broken"


async def test_merged_pr_with_review_policy_always_interrupts_before_delivery(
    model, tools, tmp_sessions
) -> None:
    """ReviewGate: review_policy 'always' must interrupt the deliver node."""
    merged_task = (
        '{"event_id": "e-merged-1", "event_type": "pull_request.merged", '
        '"project_id": "proj-1", "repository": "acme/api", "actor": "dev", '
        '"pull_request": {"number": 9, "title": "Add PKCE", "sha": "def", '
        '"merged": true, "changed_files": [{"path": "src/authly/oauth.py"}]}}'
    )
    graph = build_graph_for_run(
        "pr-review-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    result = await graph.invoke_async(
        merged_task,
        invocation_state={"run_id": "pr-review-1", "review_policy": "always"},
    )

    assert result.status == Status.INTERRUPTED
    order = [n.node_id for n in result.execution_order]
    assert "deliver" not in order


async def test_merged_pr_with_no_doc_impact_authors_nothing(
    model, tools, tmp_sessions
) -> None:
    """Impact action 'none': no writer runs and nothing is delivered."""
    merged_task = (
        '{"event_id": "e-merged-2", "event_type": "pull_request.merged", '
        '"project_id": "proj-1", "repository": "acme/api", "actor": "dev", '
        '"pull_request": {"number": 10, "title": "Internal refactor", "sha": "aaa", '
        '"merged": true}}'
    )
    graph = build_graph_for_run(
        "pr-none-1",
        surface="pull_request",
        tools_registry=tools,
        model=_none_action_model(),
        storage_dir=tmp_sessions,
    )
    result = await graph.invoke_async(
        merged_task,
        invocation_state={"run_id": "pr-none-1", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert "update" not in order
    assert "create" not in order
    assert "deliver" not in order


def _none_action_model():
    """StubModel scripted so the impact node chooses action 'none'."""
    from draftly.agents.schemas import (
        AnswerDraft,
        DeliveryReceipt,
        DocChangePlan,
        EventClassification,
        EvidenceBundle,
        ImpactAnalysis,
    )
    from tests.stub_model import StubModel

    return StubModel(
        structured_outputs={
            EventClassification: {
                "surface": "pull_request",
                "change_type": "other",
                "urgency": "low",
                "reason": "internal refactor",
            },
            EvidenceBundle: {"items": [], "summary": "no doc impact"},
            ImpactAnalysis: {
                "action": "none",
                "affected_documents": [],
                "rationale": "no public surface changed",
            },
            DocChangePlan: {"repository": "", "branch": "", "files": []},
            AnswerDraft: {"content": "", "sources": []},
            DeliveryReceipt: {
                "delivered_to": "",
                "surface": "pull_request",
                "reference": "",
                "status": "skipped",
            },
        }
    )


async def test_evaluation_graph_isolates_runner_failures() -> None:
    def broken_runner(dataset):
        raise ValueError("boom")

    graph = build_evaluation_graph(experiment_runner=broken_runner)
    result = await graph.invoke_async(
        "{}",
        invocation_state={
            "datasets": [{"name": "d", "cases": []}],
        },
    )

    assert result.status == Status.COMPLETED

    from draftly.orchestration.nodes.base import node_data

    summary = node_data(graph.state, "persist")
    assert summary["passed_all"] is False
    assert any("boom" in e for e in summary["errors"])
