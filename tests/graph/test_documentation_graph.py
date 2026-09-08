"""Documentation graph end-to-end with StubModel (plan §6.9 #1)."""

from __future__ import annotations

from strands.multiagent.base import Status

from draftly.integrations.strands.graph import build_graph_for_run
from tests.graph.conftest import PR_TASK, RELEASE_TASK


async def test_full_pipeline_with_quality_gate(model, tools, tmp_sessions) -> None:
    """A grounded change plan passes evaluation before delivery."""
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
    assert order.count("update") == 1
    assert order.count("evaluate") == 1
    assert order[4:] == [
        "update",
        "evaluate",
        "changelog",
        "changelog_evaluate",
        "deliver",
    ]
    # the wrong generation paths never ran
    assert "answer" not in order
    assert "create" not in order


def test_graph_uses_injected_agent_factory_registry(model, tools, tmp_sessions) -> None:
    from types import SimpleNamespace

    from draftly.agents.shared.classifier import build_classifier

    calls: list[object] = []

    def classifier_factory(resolved_model):
        calls.append(resolved_model)
        return build_classifier(resolved_model)

    graph = build_graph_for_run(
        "registry-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        agents=SimpleNamespace(classifier=classifier_factory),
        storage_dir=tmp_sessions,
    )

    assert graph.nodes["classify"]
    assert calls == [model]


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


def test_read_only_graph_agents_exclude_mutation_tools(model, tools, tmp_sessions) -> None:
    """Evidence-gathering agents must not receive write or delivery tools."""
    from draftly.orchestration.graphs.documentation_graph import _scope_read_only_tools

    graph = build_graph_for_run(
        "scope-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )

    forbidden = {
        "write_file",
        "update_frontmatter",
        "create_branch",
        "create_commit",
        "create_pull_request",
    }
    for node_id in ("context",):
        names = set(graph.nodes[node_id].executor.tool_names)
        assert not names & forbidden

    swarm = graph.nodes["research"].executor
    local_names = set(swarm.nodes["local_repo_researcher"].executor.tool_names)
    assert not local_names & forbidden
    assert "read_file" in local_names
    assert _scope_read_only_tools(tools.documentation_engineer)


async def test_release_event_includes_changelog_in_order(model, tools, tmp_sessions) -> None:
    """Release event: classify → context → research → impact → update → evaluate
    → changelog → changelog_evaluate → deliver."""
    graph = build_graph_for_run(
        "e2e-release-1",
        surface="pull_request",  # releases route to pull_request surface
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )

    result = await graph.invoke_async(
        RELEASE_TASK,
        invocation_state={"run_id": "e2e-release-1", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    # changelog and changelog_evaluate must appear after evaluate, before deliver
    eval_idx = order.index("evaluate")
    deliver_idx = order.index("deliver")
    assert "changelog" in order
    assert "changelog_evaluate" in order
    changelog_idx = order.index("changelog")
    changelog_eval_idx = order.index("changelog_evaluate")
    assert eval_idx < changelog_idx < changelog_eval_idx < deliver_idx


async def test_none_action_release_routes_to_changelog(model, tools, tmp_sessions) -> None:
    """action='none' on a release event skips writer but still runs changelog."""
    from draftly.agents.schemas import ImpactAnalysis

    model._structured_outputs[ImpactAnalysis] = {
        "action": "none",
        "affected_documents": [],
        "rationale": "maintenance release",
    }

    graph = build_graph_for_run(
        "e2e-release-none",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )

    result = await graph.invoke_async(
        RELEASE_TASK,
        invocation_state={"run_id": "e2e-release-none", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert "impact" in order
    # No writer nodes
    for node in ("answer", "update", "create", "evaluate"):
        assert node not in order
    # Changelog still runs
    assert "changelog" in order
    assert "changelog_evaluate" in order
    assert "deliver" in order


def _tool_names(tool_list: list) -> set[str]:
    from draftly.orchestration.graphs.tool_scoping import tool_name

    return {name for name in (tool_name(t) for t in tool_list) if name}


def test_github_grounding_gives_context_and_swarm_github_api_tools(
    model,
    tools,
    tmp_sessions,
) -> None:
    """Real-PR runs (github grounding) must reason over the GitHub API, not a
    nonexistent local checkout, so context + swarm get the read-only GitHub
    tools instead of the repository checkout tools."""
    from types import SimpleNamespace

    from draftly.agents.documentation.context import build_doc_context_agent
    from draftly.agents.documentation.research_swarm import build_doc_research_swarm

    captured: dict = {}

    def context_builder(agent_model, agent_tools, **kwargs):
        captured["context_tools"] = agent_tools
        captured["context_kwargs"] = kwargs
        return build_doc_context_agent(agent_model, agent_tools, **kwargs)

    def swarm_builder(agent_model, registry, **kwargs):
        captured["swarm_kwargs"] = kwargs
        return build_doc_research_swarm(agent_model, registry, **kwargs)

    build_graph_for_run(
        "github-grounding-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        agents=SimpleNamespace(
            documentation_context=context_builder,
            documentation_research_swarm=swarm_builder,
        ),
        storage_dir=tmp_sessions,
        grounding="github",
    )

    context_tools = _tool_names(captured["context_tools"])
    assert {"get_pull_request", "get_diff", "get_files", "code_search"} <= context_tools
    assert "git_diff" not in context_tools
    assert "read_file" not in context_tools
    assert "create_comment" not in context_tools

    swarm = captured["swarm_kwargs"]
    assert swarm["grounding"] == "github"
    assert "local_repo_researcher" not in swarm
    gh_tools = _tool_names(swarm["github_tools"] or [])
    assert {"get_pull_request", "get_diff", "get_files"} <= gh_tools
    assert "create_comment" not in gh_tools


def test_default_local_grounding_keeps_checkout_tools(
    model,
    tools,
    tmp_sessions,
) -> None:
    """Default (local-first) runs keep the repository checkout tools and no
    GitHub API tools — the offline evaluation harness depends on this."""
    from types import SimpleNamespace

    from draftly.agents.documentation.context import build_doc_context_agent
    from draftly.agents.documentation.research_swarm import build_doc_research_swarm

    captured: dict = {}

    def context_builder(agent_model, agent_tools, **kwargs):
        captured["context_tools"] = agent_tools
        return build_doc_context_agent(agent_model, agent_tools, **kwargs)

    def swarm_builder(agent_model, registry, **kwargs):
        captured["swarm_kwargs"] = kwargs
        return build_doc_research_swarm(agent_model, registry, **kwargs)

    build_graph_for_run(
        "local-grounding-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        agents=SimpleNamespace(
            documentation_context=context_builder,
            documentation_research_swarm=swarm_builder,
        ),
        storage_dir=tmp_sessions,
    )

    context_tools = _tool_names(captured["context_tools"])
    assert {"read_file", "git_diff", "git_status"} <= context_tools
    assert "get_pull_request" not in context_tools

    swarm = captured["swarm_kwargs"]
    assert swarm["grounding"] == "local"
    assert (swarm["github_tools"] or []) == []
    assert _tool_names(swarm["local_tools"] or []) >= {"read_file", "git_diff"}


def test_documentation_graph_timeout_budget_covers_a_delivered_run(
    model,
    tools,
    tmp_sessions,
) -> None:
    """Regression: a delivered PR docs run spans ~8 sequential LLM nodes and
    the live run died at 600s before the ReviewGate could fire. The graph
    ceiling must leave headroom so a healthy run reaches pending_review."""
    graph = build_graph_for_run(
        "timeout-budget-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )

    assert graph.execution_timeout >= 1800


async def test_memory_grounded_context_reaches_delivery(
    model,
    tools,
    tmp_sessions,
) -> None:
    """Regression: memory-wrapping the context node dropped its EvidenceBundle
    (an empty MultiAgentResult), so evaluation never saw evidence and the run
    looped in revision until the timeout killed it before delivery."""
    from types import SimpleNamespace

    class Bundle:
        async def knowledge(self, query: str, *, org_id: str | None = None):
            del query, org_id
            return [{"content": "OAuth exchange adds a new public API"}]

        async def episodes(self, query: str, *, limit: int = 2, org_id: str | None = None):
            del query, org_id, limit
            return []

        async def procedures(self, query: str, *, limit: int = 1, org_id: str | None = None):
            del query, org_id, limit
            return []

    graph = build_graph_for_run(
        "memory-grounded-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        memory=SimpleNamespace(
            knowledge=Bundle().knowledge,
            episodes=Bundle().episodes,
            procedures=Bundle().procedures,
        ),
    )

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "memory-grounded-1", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert order[-1] == "deliver"
    assert order.count("update") == 1
    assert order.count("evaluate") == 1


def test_documentation_graph_wires_required_rubric_graders(model, tools, tmp_sessions) -> None:
    """The rubric grader is mandatory production wiring: both the docs
    evaluator and the changelog evaluator are built with a non-None grader."""
    graph = build_graph_for_run(
        "grader-required-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )

    evaluator = graph.nodes["evaluate"].executor
    assert evaluator.rubric_grader is not None

    changelog_evaluator = graph.nodes["changelog_evaluate"].executor
    assert changelog_evaluator.rubric_grader is not None
