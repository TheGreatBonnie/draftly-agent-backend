"""Documentation graph end-to-end with StubModel (plan §6.9 #1)."""

from __future__ import annotations

from strands.multiagent.base import Status

from draftly.integrations.strands.graph import build_graph_for_run
from tests.graph.conftest import PR_TASK, RELEASE_TASK, stub_model
from tests.stub_model import StubModel

PLAN_CONTENT_MARKER = "widgets docs/widgets.md widgets"
PLAN_PATH_MARKER = "docs/widgets.md"
CHANGELOG_MARKER = "## [v2.0.0] - 2026-09-04"


def _flatten_messages(messages: list) -> str:
    """Concatenate the text of every content block in a message list."""
    chunks: list[str] = []
    for message in messages or []:
        for block in message.get("content") or []:
            if isinstance(block, dict):
                text = block.get("text")
                if text:
                    chunks.append(text)
    return "\n".join(chunks)


class RecordingStubModel(StubModel):
    """StubModel that records the full text of every delivery prompt it sees.

    The delivery agent runs with a forced ``DeliveryReceipt`` structured tool,
    so a stream call whose tool_specs contain that name is the delivery agent
    receiving its node input — capture it and let StubModel continue.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.delivery_prompts: list[str] = []

    async def stream(
        self,
        messages,
        tool_specs=None,
        system_prompt=None,
        *,
        tool_choice=None,
        system_prompt_content=None,
        invocation_state=None,
        **kwargs,
    ):
        if any(spec.get("name") == "DeliveryReceipt" for spec in (tool_specs or [])):
            self.delivery_prompts.append(_flatten_messages(messages))
        async for event in super().stream(
            messages,
            tool_specs=tool_specs,
            system_prompt=system_prompt,
            tool_choice=tool_choice,
            system_prompt_content=system_prompt_content,
            invocation_state=invocation_state,
            **kwargs,
        ):
            yield event


async def test_full_pipeline_with_quality_gate(
    model, tools, tmp_sessions, comment_factory
) -> None:
    """A grounded change plan passes evaluation before delivery; a PR notify
    comment (draft-then-post) runs in parallel off impact."""
    factory, commenter = comment_factory
    graph = build_graph_for_run(
        "e2e-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
    )

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "e2e-1", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert order[0] == "classify"
    assert order[-1] == "deliver"
    assert order.count("document") == 1
    assert order.count("evaluate") == 1
    # generate → evaluate → changelog → changelog gate → deliver keep their
    # relative order (the strict list prefix/order changed because notify runs
    # in parallel from impact, so assert membership + relative order instead)
    assert order.index("impact") < order.index("document") < order.index("evaluate")
    assert order.index("evaluate") < order.index("changelog")
    assert order.index("changelog") < order.index("changelog_evaluate")
    assert order.index("changelog_evaluate") < order.index("deliver")
    # the notify branch run in parallel and posted the scripted comment once
    assert order.index("impact") < order.index("notify") < order.index("notify_post")
    assert commenter.calls == [
        (
            "acme/api",
            7,
            "Draftly will generate docs for this PR:\n- docs/widgets.md",
        )
    ]
    # the wrong generation path never ran
    assert "answer" not in order


def test_graph_uses_injected_agent_factory_registry(model, tools, tmp_sessions) -> None:
    from types import SimpleNamespace

    from draftly.agents.shared.classifier import build_classifier

    calls: list[object] = []

    def classifier_factory(resolved_model, **kwargs):
        calls.append(resolved_model)
        return build_classifier(resolved_model, **kwargs)

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


async def test_impact_none_skips_generation_and_delivery(
    model, tools, tmp_sessions, comment_factory
) -> None:
    """action='none' fans out to no generation node; graph completes. The PR
    notify branch still runs (event is pull_request.opened) and posts."""
    from draftly.agents.schemas import ImpactAnalysis

    factory, commenter = comment_factory
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
        comment_factory=factory,
    )

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "e2e-3", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert "impact" in order
    # notify/notify_post are a parallel branch and fire on opened events
    assert "notify" in order
    assert "notify_post" in order
    assert commenter.calls == [
        (
            "acme/api",
            7,
            "Draftly will generate docs for this PR:\n- docs/widgets.md",
        )
    ]
    for node in ("answer", "document", "evaluate", "deliver"):
        assert node not in order


async def test_graph_builds_single_document_fanout_writer_node(
    model, tools, tmp_sessions
) -> None:
    """The write path is a single ``document`` fan-out node; the update/create
    node split is gone."""
    from draftly.orchestration.nodes.fan_out import FanOutWriterNode

    graph = build_graph_for_run(
        "e2e-4",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    assert isinstance(graph.nodes["document"].executor, FanOutWriterNode)
    assert "update" not in graph.nodes
    assert "create" not in graph.nodes


def test_graph_uses_document_fanout_node_not_update_create(model, tools, tmp_sessions) -> None:
    """The docs graph wires the document fan-out node and never builds the
    legacy update/create writer nodes."""
    graph = build_graph_for_run(
        "fanout-node-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )

    assert "document" in graph.nodes
    assert "update" not in graph.nodes
    assert "create" not in graph.nodes


async def test_graph_runs_review_between_document_and_evaluate(
    model, tools, tmp_sessions, comment_factory
) -> None:
    """The docs graph wires a ``review`` node between the fan-out writer and
    evaluation: a completed document alone never opens the evaluation gate —
    the review verdict (``eval_ready``) does, so evaluation stays false
    while the writer is mid-correction."""
    factory, _ = comment_factory
    graph = build_graph_for_run(
        "review-wired-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
    )
    assert "review" in graph.nodes

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "review-wired-1", "review_policy": "never"},
    )
    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert order.index("document") < order.index("review") < order.index("evaluate")


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


async def test_writer_draft_tools_appended_only_to_doc_authoring_node(
    model, tools, tmp_sessions
) -> None:
    """The three draft persistence tools (start_draft/append_chunk/
    finalize_draft) are appended ONLY to the docs writer factory behind the
    document fan-out node; the answer/changelog writers and the deliver agent
    must not author into the store."""
    graph = build_graph_for_run(
        "draft-tools-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    draft_tools = {"start_draft", "append_chunk", "finalize_draft"}
    doc_node = graph.nodes["document"].executor
    assert draft_tools <= _tool_names(doc_node._factory.tools), (
        "document writer factory missing draft tools"
    )
    for node_id in ("answer", "changelog", "deliver"):
        assert not draft_tools & set(graph.nodes[node_id].executor.tool_names), (
            f"{node_id} must not get draft tools"
        )


def test_deliver_agent_gets_drafted_docs_read_tool(model, tools, tmp_sessions) -> None:
    """The delivery (github) agent must be able to fetch store bodies via the
    read-only get_drafted_docs tool; writer nodes never see it (they author
    into the store, they don't read it)."""
    graph = build_graph_for_run(
        "deliver-tools-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    deliver_names = set(graph.nodes["deliver"].executor.tool_names)
    assert "get_drafted_docs" in deliver_names
    doc_node = graph.nodes["document"].executor
    assert "get_drafted_docs" not in _tool_names(doc_node._factory.tools)
    assert "get_drafted_docs" not in set(graph.nodes["answer"].executor.tool_names)


def test_draft_generation_hook_registered_as_provider(
    model, tools, tmp_sessions, monkeypatch
) -> None:
    """The drafts-seeding NextGenerationHook is wired into the docs graph build,
    so the runner's seed flows into scope for every iterative writer pass."""
    import draftly.orchestration.graphs.documentation_graph as docs_graph

    constructed: list = []
    real_hook = docs_graph.NextGenerationHook

    class _SpyHook(real_hook):
        def __init__(self, *args, **kwargs):  # noqa: D401
            constructed.append(self)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(docs_graph, "NextGenerationHook", _SpyHook)
    build_graph_for_run(
        "hook-wired-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    assert constructed, "NextGenerationHook must be instantiated by the graph build"


async def test_deliver_gate_blocks_delivery_without_sealed_drafts(
    model, tools, tmp_sessions, comment_factory, empty_drafts
) -> None:
    """Injecting a draft store that never sealed a generation keeps the deliver
    node from running: the changelog_evaluate → deliver edge requires
    ``has_drafts`` from the evaluator (the sealed revision check)."""
    factory, _ = comment_factory
    graph = build_graph_for_run(
        "deliver-blocked",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
        drafts_repo=empty_drafts,
    )

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "deliver-blocked", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert "deliver" not in order, "delivery must wait for a sealed draft generation"
    assert empty_drafts.calls, "evaluator must consult the draft store"


async def test_deliver_gate_runs_with_sealed_drafts(
    model, tools, tmp_sessions, comment_factory, sealed_drafts
) -> None:
    """A sealed store generation unblocks delivery; the changelog gate still
    determines ordering (deliver never fires before changelog evaluation)."""
    factory, _ = comment_factory
    graph = build_graph_for_run(
        "deliver-gated",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
        drafts_repo=sealed_drafts,
    )

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "deliver-gated", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert "deliver" in order, "sealed drafts must unblock delivery"
    assert order.index("changelog_evaluate") < order.index("deliver")


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
    """Release event: classify → context → research → impact → document
    → evaluate → changelog → changelog_evaluate → deliver."""
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
    # PR notify is PR-opened only: releases must not draft or post
    assert "notify" not in order
    assert "notify_post" not in order


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
    for node in ("answer", "document", "evaluate"):
        assert node not in order
    # Changelog still runs
    assert "changelog" in order
    assert "changelog_evaluate" in order
    assert "deliver" in order
    # PR notify is PR-opened only: releases must not draft or post
    assert "notify" not in order
    assert "notify_post" not in order


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
    assert {
        "get_pull_request",
        "get_diff",
        "get_files",
        "github_search_code",
        "github_read_file",
        "github_get_tree",
    } <= context_tools
    assert "code_search" not in context_tools
    assert "git_diff" not in context_tools
    assert "read_file" not in context_tools
    assert "create_comment" not in context_tools

    swarm = captured["swarm_kwargs"]
    assert swarm["grounding"] == "github"
    assert "local_repo_researcher" not in swarm
    gh_tools = _tool_names(swarm["github_tools"] or [])
    assert {"get_pull_request", "get_diff", "get_files"} <= gh_tools
    assert "create_comment" not in gh_tools
    # The graph's configured Strands budgets must reach the swarm so the
    # research agent gets the full node budget instead of a hardcoded ceiling.
    assert swarm["execution_timeout"] == 3600.0
    assert swarm["node_timeout"] == 1200.0


def test_github_grounding_impact_agent_uses_api_repo_tools(
    model,
    tools,
    tmp_sessions,
) -> None:
    """Impact (github grounding) must analyze the API repo, not walk a
    nonexistent local checkout: no code_search, and no read-only local fs/git."""
    from types import SimpleNamespace

    from draftly.agents.documentation.analyzer import build_impact_agent

    captured: dict = {}

    def impact_builder(agent_model, agent_tools, **kwargs):
        captured["impact_tools"] = agent_tools
        return build_impact_agent(agent_model, agent_tools, **kwargs)

    build_graph_for_run(
        "github-impact-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        agents=SimpleNamespace(impact_agent=impact_builder),
        storage_dir=tmp_sessions,
        grounding="github",
    )

    impact_tools = _tool_names(captured["impact_tools"])
    assert "code_search" not in impact_tools
    assert "read_file" not in impact_tools
    assert "list_directory" not in impact_tools
    assert "git_diff" not in impact_tools
    assert "git_status" not in impact_tools
    expected_github_tools = {
        "get_diff",
        "get_files",
        "github_search_code",
        "github_read_file",
        "github_get_tree",
    }
    assert expected_github_tools <= impact_tools
    assert {"semantic_search", "keyword_search", "hybrid_search"} <= impact_tools


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
    comment_factory,
) -> None:
    """Regression: memory-wrapping the context node dropped its EvidenceBundle
    (an empty MultiAgentResult), so evaluation never saw evidence and the run
    looped in revision until the timeout killed it before delivery."""
    from types import SimpleNamespace

    factory, _ = comment_factory

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
        comment_factory=factory,
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
    assert order.count("document") == 1
    assert order.count("evaluate") == 1
    assert "notify_post" in order


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


async def test_deliver_prompt_contains_approved_plan_and_changelog(
    tools, tmp_sessions, comment_factory
) -> None:
    """The delivered content must actually reach the delivery agent's prompt.

    Regression: update/create/answer/changelog results are only passed to the
    deliver node as prompt input if a directed edge exists between the two.
    Without them the delivery agent only sees the bare changelog_evaluate
    verdict, so the approved DocChangePlan metadata (``files[].path``) and the
    changelog markdown never appear in the prompt. File bodies are no longer
    inline: the deliver agent fetches them from the draft store via
    ``get_drafted_docs`` (Task 7), so this asserts the plan metadata reaches
    the prompt.
    """
    factory, _ = comment_factory
    recording = RecordingStubModel(
        structured_outputs=stub_model()._structured_outputs
    )
    graph = build_graph_for_run(
        "deliver-content-1",
        surface="pull_request",
        tools_registry=tools,
        model=recording,
        storage_dir=tmp_sessions,
        comment_factory=factory,
    )

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "deliver-content-1", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    # delivery must not fire early from the new content-carrying edges: it
    # still waits for the changelog gate
    assert order.index("changelog_evaluate") < order.index("deliver")

    assert recording.delivery_prompts, "delivery agent never ran"
    prompt_text = "\n".join(recording.delivery_prompts)
    assert PLAN_PATH_MARKER in prompt_text, (
        "approved DocChangePlan metadata (file paths) missing from deliver prompt"
    )
    assert CHANGELOG_MARKER in prompt_text, (
        "changelog markdown missing from deliver prompt"
    )
