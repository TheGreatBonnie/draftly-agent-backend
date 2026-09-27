"""Documentation graph end-to-end with StubModel (plan §6.9 #1).

The docs branch is one durable ``DocumentationWorkflowNode`` (page-scoped
write/evaluate/review inside the workflow) feeding ``changelog`` /
``changelog_evaluate`` / ``deliver``. The former ``document → review →
evaluate`` loop nodes are gone; the deterministic quality gate lives inside
the page workflow via ``PageEvaluatorHandler``.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from strands.multiagent.base import Status

from draftly.integrations.strands.graph import build_graph_for_run
from tests.graph.conftest import (
    PR_TASK,
    RELEASE_TASK,
    docs_model,
    docs_workflow_wiring,
    two_page_model,
)
from tests.stub_model import StubModel

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


async def test_full_pipeline_with_quality_gate(model, tools, tmp_sessions, comment_factory) -> None:
    """A grounded change plan passes the page workflow, gets a changelog, and
    is delivered; the PR notify branch (draft-then-post) runs in parallel."""
    factory, commenter = comment_factory
    wiring = docs_workflow_wiring()
    graph = build_graph_for_run(
        "e2e-1",
        surface="pull_request",
        tools_registry=tools,
        model=docs_model(),
        storage_dir=tmp_sessions,
        comment_factory=factory,
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
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
    # document → changelog → changelog gate → deliver keep their relative
    # order (the strict list prefix shifted because notify runs in parallel
    # from impact, so assert membership + relative order instead)
    assert order.index("impact") < order.index("document") < order.index("changelog")
    assert order.index("changelog") < order.index("changelog_evaluate")
    assert order.index("changelog_evaluate") < order.index("deliver")
    # the notify branch ran in parallel and posted the scripted comment once
    assert order.index("impact") < order.index("notify_post")
    assert commenter.calls == [
        (
            "acme/api",
            7,
            "Draftly detected documentation work for this PR:\n- docs/widgets.md",
        )
    ]
    # the wrong generation path never ran
    assert "answer" not in order
    assert "answer_evaluate" not in order


async def test_graph_runs_document_workflow_node_not_legacy_loop(
    model, tools, tmp_sessions, comment_factory
) -> None:
    """The docs graph wires one durable DocumentationWorkflowNode; the legacy
    update/create writer split, the shared evaluate node, and the review node
    are gone (evaluation + cross-page review run inside the workflow)."""
    from draftly.orchestration.page_workflow.node import DocumentationWorkflowNode

    factory, _ = comment_factory
    wiring = docs_workflow_wiring()
    graph = build_graph_for_run(
        "loop-gone-1",
        surface="pull_request",
        tools_registry=tools,
        model=docs_model(),
        storage_dir=tmp_sessions,
        comment_factory=factory,
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
    )

    assert isinstance(graph.nodes["document"].executor, DocumentationWorkflowNode)
    for node in ("update", "create", "review", "evaluate"):
        assert node not in graph.nodes

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "loop-gone-1", "review_policy": "never"},
    )
    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert order.count("document") == 1
    assert "changelog_evaluate" in order
    assert order.index("changelog_evaluate") < order.index("deliver")


def test_graph_builds_single_document_workflow_node(model, tools, tmp_sessions) -> None:
    """The write path is the single ``document`` node; update/create are gone."""
    from draftly.orchestration.page_workflow.node import DocumentationWorkflowNode

    graph = build_graph_for_run(
        "e2e-4",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    assert isinstance(graph.nodes["document"].executor, DocumentationWorkflowNode)
    assert "update" not in graph.nodes
    assert "create" not in graph.nodes


def test_graph_uses_injected_agent_factory_registry(model, tools, tmp_sessions) -> None:
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
    # notify_post is a parallel branch and fires on opened events
    assert "notify_post" in order
    assert commenter.calls == [
        (
            "acme/api",
            7,
            "Draftly found no documentation changes needed for this PR.",
        )
    ]
    for node in ("answer", "answer_evaluate", "document", "changelog", "deliver"):
        assert node not in order


async def test_document_without_workflow_fails_without_delivery(
    model, tools, tmp_sessions, comment_factory
) -> None:
    """Offline fixtures (no page_workflow) fail the document node fast instead
    of fabricating unsealed pages: the graph never reaches delivery."""
    factory, _ = comment_factory
    graph = build_graph_for_run(
        "offline-doc-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
    )

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "offline-doc-1", "review_policy": "never"},
    )

    assert result.status == Status.FAILED
    order = [n.node_id for n in result.execution_order]
    assert "document" in order
    assert "deliver" not in order


async def test_document_escalation_routes_to_human_review_without_changelog(
    model, tools, tmp_sessions, comment_factory
) -> None:
    """Impact tasks without page-scoped evidence settle the page as
    AWAITING_HUMAN_REVIEW: the workflow reports passed=False ready_for_review,
    the docs branch skips the changelog, and the change routes to the graph's
    delivery gate (deliver itself runs under review_policy never)."""
    factory, _ = comment_factory
    wiring = docs_workflow_wiring()
    graph = build_graph_for_run(
        "escalate-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
    )

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "escalate-1", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert order.count("document") == 1
    assert "changelog" not in order
    assert "changelog_evaluate" not in order
    assert order.index("document") < order.index("deliver")


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


def test_writer_draft_tools_appended_only_to_doc_authoring_node(model, tools, tmp_sessions) -> None:
    """The three draft persistence tools (start_draft/append_chunk/
    finalize_draft) are appended ONLY to the docs writer factory behind the
    page workflow; the answer/changelog writers and the deliver agent must not
    author into the store."""
    wiring = docs_workflow_wiring()
    graph = build_graph_for_run(
        "draft-tools-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
    )
    draft_tools = {"start_draft", "append_chunk", "finalize_draft"}
    doc_node = graph.nodes["document"].executor
    assert draft_tools <= _tool_names(doc_node.handlers["write"].writer_factory.tools), (
        "page-workflow writer factory missing draft tools"
    )
    for node_id in ("answer", "changelog", "deliver"):
        assert not draft_tools & set(graph.nodes[node_id].executor.tool_names), (
            f"{node_id} must not get draft tools"
        )


def test_deliver_agent_gets_drafted_docs_read_tool(model, tools, tmp_sessions) -> None:
    """The delivery (github) agent must be able to fetch store bodies via the
    read-only get_drafted_docs tool; the answer writer never sees it."""
    graph = build_graph_for_run(
        "deliver-tools-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    deliver_names = set(graph.nodes["deliver"].executor.tool_names)
    assert "get_drafted_docs" in deliver_names
    assert "get_drafted_docs" not in set(graph.nodes["answer"].executor.tool_names)
    changelog_names = set(graph.nodes["changelog"].executor.tool_names)
    assert "get_drafted_docs" not in changelog_names


def test_github_grounding_changelog_prompt_names_only_registered_read_tools(
    model, tools, tmp_sessions
) -> None:
    """The changelog prompt must name only read tools actually registered: in
    github grounding that is the GitHub API suite, never the local
    ``read_file`` the old prompt told it to call."""
    from draftly.agents.documentation.changelog import build_changelog_agent

    captured: dict = {}

    def changelog_builder(agent_model, agent_tools, **kwargs):
        agent = build_changelog_agent(agent_model, agent_tools, **kwargs)
        captured["prompt"] = agent.system_prompt
        captured["tools"] = agent_tools
        return agent

    build_graph_for_run(
        "github-changelog-prompt-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        grounding="github",
        agents=SimpleNamespace(changelog_agent=changelog_builder),
    )

    assert "github_read_file" in _tool_names(captured["tools"])
    prompt = captured["prompt"]
    assert "github_read_file" in prompt
    assert "read_file" not in prompt.replace("github_read_file", "")
    assert "list_directory" not in prompt
    assert "registered toolset" in prompt


def test_writer_limits_forwarded_to_page_writer_handler(model, tools, tmp_sessions) -> None:
    """Explicit Strands writer budgets reach the page writer handler.

    The writer must invoke_async with Strands ``limits`` so an unbounded loop
    stops at a deterministic turn/output cap instead of relying on the
    provider's per-response truncation recovery (which starves the agent and
    made it invent unregistered tool names in run 3ef0d570).
    """
    wiring = docs_workflow_wiring()
    graph = build_graph_for_run(
        "writer-limits-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
        writer_limits={"turns": 3, "output_tokens": 500},
    )
    write_handler = graph.nodes["document"].executor.handlers["write"]
    assert write_handler.limits == {"turns": 3, "output_tokens": 500}


def test_doc_writer_skills_list_only_registered_writer_tools(model, tools, tmp_sessions) -> None:
    """Both doc-writing SKILLs must only advertise tools the writer actually
    registers in github grounding.

    ``documentation-update`` used to advertise ``read_file``/``write_file``/
    ``update_frontmatter`` — none of which the GitHub-mode writer registers.
    Teaching the model those names produced the repeated read_file /
    list_files tool-not-found loop in run d7cfb2a0.
    """
    skills_root = Path(__file__).resolve().parents[2] / "src/draftly/skills"
    wiring = docs_workflow_wiring()
    graph = build_graph_for_run(
        "skill-writer-tools-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        grounding="github",
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
    )
    writer_names = _tool_names(
        graph.nodes["document"].executor.handlers["write"].writer_factory.tools
    )
    for skill_name in ("documentation-generation", "documentation-update"):
        text = (skills_root / skill_name / "SKILL.md").read_text(encoding="utf-8")
        allowed = set(re.findall(r"^allowed-tools:\s*(.+)$", text, re.M)[0].split())
        assert "read_file" not in allowed, f"{skill_name} must not advertise local-only read_file"
        unregistered = allowed - writer_names
        assert not unregistered, (
            f"{skill_name} advertises unregistered writer tools: {unregistered}"
        )


def test_github_grounding_docs_skills_advertise_only_registered_tools(
    model,
    tools,
    tmp_sessions,
) -> None:
    """Skills attached to PR-workflow agents (impact, context) may only
    advertise tools those agents actually register in github grounding.

    ``documentation-research`` advertised local-only ``read_file`` to an
    impact/context agent that never has it — the same phantom-tool failure
    class as the writer's ``documentation-update`` skill (run d7cfb2a0).
    """
    from draftly.agents.documentation.analyzer import build_impact_agent
    from draftly.agents.documentation.context import build_doc_context_agent

    skills_root = Path(__file__).resolve().parents[2] / "src/draftly/skills"
    captured: dict = {}

    def impact_builder(agent_model, agent_tools, **kwargs):
        captured["impact_tools"] = agent_tools
        return build_impact_agent(agent_model, agent_tools, **kwargs)

    def context_builder(agent_model, agent_tools, **kwargs):
        captured["context_tools"] = agent_tools
        return build_doc_context_agent(agent_model, agent_tools, **kwargs)

    build_graph_for_run(
        "github-skills-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        agents=SimpleNamespace(
            impact_agent=impact_builder,
            documentation_context=context_builder,
        ),
        storage_dir=tmp_sessions,
        grounding="github",
    )

    local_only = {
        "read_file",
        "list_directory",
        "write_file",
        "update_frontmatter",
        "file_exists",
        "code_search",
        "git_diff",
        "git_log",
        "git_status",
    }
    expectations = {
        "impact": (
            captured["impact_tools"],
            ("documentation-research", "github-pr-analysis"),
        ),
        "context": (
            captured["context_tools"],
            ("documentation-research", "documentation-gap-detection"),
        ),
    }
    for agent_label, (registered, skill_names) in expectations.items():
        registered_names = _tool_names(registered)
        for skill_name in skill_names:
            text = (skills_root / skill_name / "SKILL.md").read_text(encoding="utf-8")
            allowed = set(re.findall(r"^allowed-tools:\s*(.+)$", text, re.M)[0].split())
            bad = allowed & local_only
            assert not bad, (
                f"{skill_name} advertises local-only tools the {agent_label} "
                f"agent never registers in github grounding: {bad}"
            )
            unregistered = allowed - registered_names
            assert not unregistered, (
                f"{skill_name} advertises tools the {agent_label} agent never "
                f"registers in github grounding: {unregistered}"
            )


def test_github_grounding_writer_and_changelog_use_api_repo_tools(
    model, tools, tmp_sessions
) -> None:
    """PR (github) runs have no local checkout: the writer and changelog must
    still be able to read current docs, so both get the read-only GitHub-API
    repo tools.

    Without a read path the writer looped calling unregistered ``read_file`` /
    ``list_files`` until the graph killed it (run d7cfb2a0).
    """
    wiring = docs_workflow_wiring()
    graph = build_graph_for_run(
        "github-writer-tools-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        grounding="github",
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
    )
    writer_names = _tool_names(
        graph.nodes["document"].executor.handlers["write"].writer_factory.tools
    )
    changelog_names = set(graph.nodes["changelog"].executor.tool_names)
    expected_github = {"github_read_file", "github_get_tree"}
    for label, names in (("writer", writer_names), ("changelog", changelog_names)):
        assert expected_github <= names, (
            f"{label} missing GitHub API read tools in github grounding"
        )
        assert not names & {
            "read_file",
            "list_directory",
            "git_diff",
            "git_status",
        }, f"{label} still has local-checkout tools in github grounding"
    assert "github_search_code" not in writer_names
    # draft persistence stays writer-only
    assert {"start_draft", "append_chunk", "finalize_draft"} <= writer_names
    assert not {"start_draft", "append_chunk", "finalize_draft"} & changelog_names


def test_draft_generation_hook_registered_as_provider(
    model, tools, tmp_sessions, monkeypatch
) -> None:
    """The drafts-seeding NextGenerationHook is wired into the docs graph build."""
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


async def test_revision_loop_retries_only_failed_page(tools, tmp_sessions, comment_factory) -> None:
    """The full revise loop targets only evaluate-failed pages: page b fails
    the deterministic gate (content 'TODO'), the page workflow schedules ONE
    revision for docs/b.md, docs/a.md is untouched, the cross-page reviewer
    runs once over the settled set, then changelog → deliver completes."""
    factory, _ = comment_factory
    wiring = docs_workflow_wiring(
        topics={"docs/a.md": "alpha", "docs/b.md": "beta"},
        fail_versions={"docs/b.md": {1}},
        with_reviewer=True,
    )
    graph = build_graph_for_run(
        "revise-subset",
        surface="pull_request",
        tools_registry=tools,
        model=two_page_model(),
        storage_dir=tmp_sessions,
        comment_factory=factory,
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
    )

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "revise-subset", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert order.count("document") == 1
    assert order.index("changelog_evaluate") < order.index("deliver")

    sealed = sorted(wiring["writer_recorder"], key=lambda row: (row[0], row[1]))
    assert [row[:2] for row in sealed] == [
        ("docs/a.md", 1),
        ("docs/b.md", 1),
        ("docs/b.md", 2),
    ], wiring["writer_recorder"]
    first_batch = [prompt for (_, version, prompt) in sealed if version == 1]
    assert any("Path: docs/a.md" in prompt for prompt in first_batch)
    assert any("Path: docs/b.md" in prompt for prompt in first_batch)
    revisions = [prompt for (_, version, prompt) in sealed if version == 2]
    assert len(revisions) == 1
    assert "Revise documentation task: docs/b.md" in revisions[0]
    assert "Path: docs/b.md" in revisions[0]
    assert "Path: docs/a.md" not in revisions[0]

    assert wiring["review_recorder"], "cross-page reviewer never ran"
    assert len(wiring["review_recorder"]) == 1


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


async def test_release_event_includes_changelog_in_order(
    tools, tmp_sessions, comment_factory
) -> None:
    """Release event: document (page workflow passes) → changelog →
    changelog_evaluate → deliver; no PR notify branch on releases."""
    factory, _ = comment_factory
    wiring = docs_workflow_wiring()
    graph = build_graph_for_run(
        "e2e-release-1",
        surface="pull_request",  # releases route to pull_request surface
        tools_registry=tools,
        model=docs_model(),
        storage_dir=tmp_sessions,
        comment_factory=factory,
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
    )

    result = await graph.invoke_async(
        RELEASE_TASK,
        invocation_state={"run_id": "e2e-release-1", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    doc_idx = order.index("document")
    changelog_idx = order.index("changelog")
    changelog_eval_idx = order.index("changelog_evaluate")
    deliver_idx = order.index("deliver")
    assert doc_idx < changelog_idx < changelog_eval_idx < deliver_idx
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
    for node in ("answer", "answer_evaluate", "document"):
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


def test_github_grounding_impact_prompt_names_only_registered_tools(
    model,
    tools,
    tmp_sessions,
) -> None:
    """The impact prompt must only name repo tools actually registered for the
    run's grounding — no phantom local-checkout names in github mode."""
    from draftly.agents.documentation.analyzer import build_impact_agent

    captured: dict = {}

    def impact_builder(agent_model, agent_tools, **kwargs):
        agent = build_impact_agent(agent_model, agent_tools, **kwargs)
        captured["prompt"] = agent.system_prompt
        return agent

    build_graph_for_run(
        "github-impact-2",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        agents=SimpleNamespace(impact_agent=impact_builder),
        storage_dir=tmp_sessions,
        grounding="github",
    )

    prompt = captured["prompt"]
    assert "github_get_tree" in prompt
    assert "github_read_file" in prompt
    assert "list_directory" not in prompt
    assert "list_directory, github_read_file, read_file" not in prompt
    # guardrail: never invent tool names (run-level skills.analysis.analyze_pr
    # hallucination) — only registered tools may be called
    assert "registered toolset" in prompt


def test_default_local_grounding_has_no_github_tools_in_impact_prompt(
    model,
    tools,
    tmp_sessions,
) -> None:
    """Local-first runs must not instruct the impact agent to call GitHub API
    repo tools that are not registered (no installation token offline)."""
    from draftly.agents.documentation.analyzer import build_impact_agent

    captured: dict = {}

    def impact_builder(agent_model, agent_tools, **kwargs):
        agent = build_impact_agent(agent_model, agent_tools, **kwargs)
        captured["prompt"] = agent.system_prompt
        return agent

    build_graph_for_run(
        "local-impact-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        agents=SimpleNamespace(impact_agent=impact_builder),
        storage_dir=tmp_sessions,
    )

    prompt = captured["prompt"]
    assert "github_get_tree" not in prompt
    assert "github_read_file" not in prompt
    # the run's actual repo tools are named instead
    assert "get_files" in prompt
    assert "code_search" in prompt


def _unavailable_sentence(prompt: str) -> str:
    """The prompt's "these tools are NOT available" line, or "" if absent.

    Lets a test assert that an unregistered tool name appears *only* as an
    explicit prohibition, without depending on the sentence's exact wording.
    """
    for line in prompt.splitlines():
        if "NOT available in this run" in line:
            return line
    return ""


def test_github_grounding_writer_prompt_names_only_registered_tools(
    model, tools, tmp_sessions
) -> None:
    """The writer system prompt must name only repo tools registered for the
    run: in github grounding the read hint is the GitHub-API suite, never the
    local checkout names the writer does not have (run d7cfb2a0 looped because
    the model was taught read_file that was never registered)."""
    from draftly.agents.documentation.writer import build_writer_agent

    wiring = docs_workflow_wiring()
    graph = build_graph_for_run(
        "github-writer-prompt-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        grounding="github",
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
    )
    factory = graph.nodes["document"].executor.handlers["write"].writer_factory
    agent = build_writer_agent(
        factory.model,
        factory.tools,
        runtime=factory.runtime,
        agent_id="documentation.writer",
        node_id="document",
    )
    prompt = agent.system_prompt
    assert "github_get_tree" in prompt
    assert "github_read_file" in prompt
    assert "registered toolset" in prompt
    # Absent repo tools ARE named, but only inside the "not available"
    # sentence. Run d7cfb2a0 looped on a silently-missing `read_file`; run
    # d76e2490 looped harder, calling it 683 times. Silence did not teach the
    # model, an explicit prohibition does.
    assert _unavailable_sentence(prompt)
    unavailable = _unavailable_sentence(prompt)
    assert "`read_file`" in unavailable
    assert "`list_directory`" in unavailable
    # Nothing outside that sentence may teach an unregistered name.
    assert "read_file" not in prompt.replace("github_read_file", "").replace(unavailable, "")


def test_default_local_grounding_writer_prompt_names_checkout_tools(
    model, tools, tmp_sessions
) -> None:
    """Local-first runs instruct the writer with the actual checkout tools and
    must not name GitHub-API tools that are not registered."""
    from draftly.agents.documentation.writer import build_writer_agent

    wiring = docs_workflow_wiring()
    graph = build_graph_for_run(
        "local-writer-prompt-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
    )
    factory = graph.nodes["document"].executor.handlers["write"].writer_factory
    agent = build_writer_agent(
        factory.model,
        factory.tools,
        runtime=factory.runtime,
        agent_id="documentation.writer",
        node_id="document",
    )
    prompt = agent.system_prompt
    assert "read_file" in prompt
    assert "list_directory" in prompt
    assert "registered toolset" in prompt
    # The GitHub-API names are registered nowhere in a local run, so they appear
    # only as an explicit prohibition, never as an available tool.
    unavailable = _unavailable_sentence(prompt)
    assert unavailable
    assert "`github_read_file`" in unavailable
    assert "github_read_file" not in prompt.replace(unavailable, "")


def test_default_local_grounding_keeps_checkout_tools(
    model,
    tools,
    tmp_sessions,
) -> None:
    """Default (local-first) runs keep the repository checkout tools and no
    GitHub API tools — the offline evaluation harness depends on this."""
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
    tools,
    tmp_sessions,
    comment_factory,
) -> None:
    """Regression: memory-wrapping the context node dropped its EvidenceBundle,
    so evaluation never saw evidence. The page workflow scopes evidence per
    task, so a grounded two-page run still reaches delivery."""
    factory, _ = comment_factory
    wiring = docs_workflow_wiring()

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
        model=docs_model(),
        storage_dir=tmp_sessions,
        comment_factory=factory,
        memory=SimpleNamespace(
            knowledge=Bundle().knowledge,
            episodes=Bundle().episodes,
            procedures=Bundle().procedures,
        ),
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
    )

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "memory-grounded-1", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert order[-1] == "deliver"
    assert order.count("document") == 1
    assert "notify_post" in order


def test_documentation_graph_wires_required_rubric_graders(model, tools, tmp_sessions) -> None:
    """The rubric grader is mandatory production wiring: the page-workflow
    evaluator and the changelog evaluator are both built with a non-None
    grader."""
    wiring = docs_workflow_wiring()
    graph = build_graph_for_run(
        "grader-required-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
    )

    document_node = graph.nodes["document"].executor
    page_evaluator = document_node.handlers["evaluate"]
    assert page_evaluator.rubric_grader is not None

    changelog_evaluator = graph.nodes["changelog_evaluate"].executor
    assert changelog_evaluator.rubric_grader is not None


async def test_deliver_prompt_contains_approved_plan_and_changelog(
    tools, tmp_sessions, comment_factory
) -> None:
    """The delivered content must actually reach the delivery agent's prompt.

    The page workflow result (``files[].path``) and the changelog markdown must
    both appear in the delivery prompt. File bodies are no longer inline: the
    deliver agent fetches them from the draft store via ``get_drafted_docs``
    (Task 7), so this asserts the plan metadata reaches the prompt.
    """
    factory, _ = comment_factory
    wiring = docs_workflow_wiring()
    recording = RecordingStubModel(structured_outputs=docs_model()._structured_outputs)
    graph = build_graph_for_run(
        "deliver-content-1",
        surface="pull_request",
        tools_registry=tools,
        model=recording,
        storage_dir=tmp_sessions,
        comment_factory=factory,
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
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
        "approved page-workflow files (paths) missing from deliver prompt"
    )
    assert CHANGELOG_MARKER in prompt_text, "changelog markdown missing from deliver prompt"
