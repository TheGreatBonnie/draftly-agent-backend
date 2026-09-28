"""The unified Draftly documentation graph.

Layout (plan §6.1)::

    classify → context → research(Swarm) → impact
      ├─(answer)─► answer ─► answer_evaluate ─(passed)────► changelog
      │                        ▲                        │
      │                        └─(answer_needs_revision)┘
      │                                                   ▼
      └─(write)──► document ──┬─(documentation_passed)─► changelog ─► changelog_evaluate ──► deliver
                             │                              │ ▲
                             └─(escalated)──► deliver         └─(changelog_needs_revision)─┘
    impact ─(none + release)─► changelog ─────────────────────┘
    impact ─(pull_request_opened)─► notify_post                           (parallel branch)

Deviations from the plan, forced by strands-agents behavior and the durable
page workflow:

- The legacy ``document → review → evaluate`` loop is replaced by one
  ``DocumentationWorkflowNode``: it seeds a durable page workflow (one
  write/evaluate pair per task, page revisions bounded at 3 attempts, a
  cross-page reviewer inside the workflow) and reports a compact
  :class:`DocumentationWorkflowResult`. Offline fixtures (``page_workflow``
  omitted) fail fast instead of fabricating unsealed bytes.
- ``answer_evaluate`` is the answer-quality gate (deterministic score +
  rubric feedback + bounded revision loop). It replaces the shared
  ``evaluate`` node; the page workflow owns all document-path evaluation.
- Escalated pages (quality budget exhausted) settle the workflow with
  ``passed=False, ready_for_review=True``; the ``document → deliver`` edge
  then routes the change to the graph-level human ReviewGate, which
  intercepts delivery until approved. The changelog is skipped on escalation
  (there is no passable changelog gate for a settled-but-failed page).
- The docs branch never runs ``deliver`` unless the page workflow settled the
  pages (``documentation_delivery_ready``); the plain changelog path supplies
  inline content and is unaffected.
- Revise edges are scoped so each loop routes back to exactly its own
  generator (``answer_needs_revision`` → answer only; the page workflow owns
  document revisions internally).
"""

from __future__ import annotations

from typing import Any

import structlog
from strands.multiagent import GraphBuilder
from strands.session.session_manager import SessionManager

from draftly.evaluation.evaluators.completeness import COMPLETENESS_RUBRIC
from draftly.evaluation.evaluators.groundedness import GROUNDEDNESS_RUBRIC
from draftly.orchestration.graphs.tool_scoping import (
    scope_read_only_tools as _scope_read_only_tools,
)
from draftly.orchestration.graphs.tool_scoping import (
    scope_writer_tools as _scope_writer_tools,
)
from draftly.orchestration.graphs.tool_scoping import tool_name as _tool_name
from draftly.orchestration.hooks.audit import RunAuditLogger
from draftly.orchestration.hooks.draft_generation import NextGenerationHook
from draftly.orchestration.hooks.review_gate import ReviewGate
from draftly.orchestration.nodes.rubric_grader import (
    build_changelog_rubric_grader,
    build_docs_rubric_grader,
)
from draftly.orchestration.routing.conditions import (
    answer_eval_passed,
    answer_needs_revision,
    changelog_needs_revision,
    delivery_content_ready,
    documentation_delivery_ready,
    documentation_passed,
    generated_changelog,
    is_valid_surface,
    none_and_release,
    pull_request_opened,
    route_to_answer,
    route_to_write,
)
from draftly.tools.documentation.drafts import (
    append_chunk,
    finalize_draft,
    get_drafted_docs,
    start_draft,
)
from draftly.tools.github.compat import github_get_file, github_list_tree
from draftly.tools.repository.code_search import code_search
from draftly.workflows.grounding import DOCS, GITHUB, LOCAL

logger = structlog.get_logger(__name__)

# Selected repository tools for the docs graph's local-first researcher. Avoids
# the GitHub API tools (get_pull_request/get_files/get_diff) that would 401 in
# the offline evaluation harness; the workspace is a local authly worktree.
_LOCAL_CODE_SEARCH = [code_search]

DEFAULT_GRAPH_ID = "draftly-main-graph"
DEFAULT_MAX_NODE_EXECUTIONS = 15
# 1200s killed delivered PR runs at the tail (8+ sequential LLM nodes plus a
# datadog-style revision loop); keep enough ceiling to reach the ReviewGate.
DEFAULT_EXECUTION_TIMEOUT = 3600.0
DEFAULT_NODE_TIMEOUT = 1200.0
DEFAULT_EVALUATOR_MAX_ITERATIONS = 2


def _dedupe(*groups: list[Any]) -> list[Any]:
    """Flatten tool groups preserving order, dropping duplicates."""
    seen: set[int] = set()
    tools: list[Any] = []
    for group in groups:
        for tool in group or []:
            if id(tool) in seen:
                continue
            seen.add(id(tool))
            tools.append(tool)
    return tools


def build_documentation_graph(
    session_manager: SessionManager | None,
    tools_registry: Any,
    model: Any,
    hooks: list[Any] | None = None,
    *,
    agents: Any = None,
    graph_id: str = DEFAULT_GRAPH_ID,
    audit_repo: Any = None,
    memory: Any = None,
    publisher: Any = None,
    jobs_repo: Any = None,
    max_node_executions: int = DEFAULT_MAX_NODE_EXECUTIONS,
    execution_timeout: float = DEFAULT_EXECUTION_TIMEOUT,
    node_timeout: float = DEFAULT_NODE_TIMEOUT,
    evaluator_max_iterations: int = DEFAULT_EVALUATOR_MAX_ITERATIONS,
    grounding: str = LOCAL,
    repo_dir: str | None = None,
    comment_factory: Any = None,
    steering_runtime: Any = None,
    drafts_repo: Any = None,
    documents_repo: Any = None,
    research_plan: Any = None,
    write_concurrency: int = 3,
    progress_sink: Any | None = None,
    page_workflow: Any = None,
    writer_limits: Any = None,
    context_limits: Any = None,
):
    """Build the unified Draftly Graph for documentation workflows.

    ``grounding`` selects the evidence mode: ``local`` (default, repository
    checkout tools), ``github`` (read-only GitHub API tools for real linked
    PRs), or ``docs`` (documentation-store search only). ``repo_dir`` is the
    concrete checkout path surfaces into the LOCAL note when known.
    ``comment_factory`` supplies the PR-notify poster (a callable returning a
    creator of ``create_comment(repository, pull_request_number, body)``);
    when omitted the node lazily builds a ``GitHubClient`` at invoke time so
    the runner's installation context applies.

    ``page_workflow`` (a ``PageWorkflowRepository``) enables the durable
    page-scoped documentation workflow. When omitted the graph runs its nodes
    offline — the ``document`` node reports a failed state rather than
    fabricating unsealed bytes.

    ``writer_limits`` forwards Strands ``limits`` (e.g. ``{"turns": 60,
    "output_tokens": 48000}``) to every page-writer invocation so an
    unbounded agent loop stops at a deterministic cap instead of relying on
    the provider's per-response truncation recovery.

    ``context_limits`` does the same for the context agent, which has no handler
    of its own. Strands' ``Graph`` invokes a node as
    ``executor.stream_async(input, invocation_state=...)`` and forwards no
    ``limits``, so the budget is carried by the node wrapper
    (``MemoryGroundedNode``).
    """
    # Lazy imports break the import cycle content_graph ↔ documentation_graph
    # (surface graphs are directly importable regardless of whether the
    # ``draftly.integrations.strands`` package has initialized). These are used
    # only here, mirroring the lazy-import pattern in the content/issue/support
    # graph builders.
    # Import agents (factories — one instance per graph node)
    from draftly.agents.documentation.analyzer import build_impact_agent
    from draftly.agents.documentation.changelog import build_changelog_agent
    from draftly.agents.documentation.context import build_doc_context_agent
    from draftly.agents.documentation.research_swarm import build_doc_research_swarm
    from draftly.agents.documentation.reviewer import build_review_agent
    from draftly.agents.documentation.writer import WriterFactory, build_writer_agent
    from draftly.agents.shared.classifier import build_classifier
    from draftly.agents.shared.delivery import build_delivery_agent
    from draftly.agents.support.answer_writer import build_answer_writer
    from draftly.app.composition.tools import filter_grounded_tools
    from draftly.integrations.strands.models import resolve_model_for_role
    from draftly.orchestration.nodes.answer_quality import AnswerQualityNode
    from draftly.orchestration.nodes.changelog_evaluate import ChangelogEvaluatorNode
    from draftly.orchestration.nodes.notify_post import NotifyPostNode
    from draftly.orchestration.page_workflow.handlers import (
        CrossPageReviewHandler,
        PageEvaluatorHandler,
        PageWriterHandler,
    )
    from draftly.orchestration.page_workflow.node import DocumentationWorkflowNode

    reg = tools_registry

    classifier_model = resolve_model_for_role(model, "classifier")
    context_model = resolve_model_for_role(model, "context")
    support_model = resolve_model_for_role(model, "support_engineer")
    research_model = resolve_model_for_role(model, "research")
    intelligence_model = resolve_model_for_role(model, "github_intelligence")
    delivery_model = resolve_model_for_role(model, "github_delivery")
    grader_model = resolve_model_for_role(model, "documentation_reviewer")
    # The same role feeds both rubric grading and the cross-page reviewer.
    reviewer_model = resolve_model_for_role(model, "documentation_reviewer")

    # Mandatory rubric graders: LLM feedback enriches the deterministic docs
    # and changelog gates on every failed draft (the future). Built eagerly
    # from the resolved reviewer model; OutputEvaluator is lazy so this is
    # safe offline.
    docs_rubric_grader = build_docs_rubric_grader(
        grader_model, rubric=GROUNDEDNESS_RUBRIC + "\n\n" + COMPLETENESS_RUBRIC
    )
    changelog_rubric_grader = build_changelog_rubric_grader(grader_model)

    registry = agents
    classifier = (getattr(registry, "classifier", None) or build_classifier)(
        classifier_model,
        runtime=steering_runtime,
        agent_id="documentation.classifier",
        node_id="classify",
    )
    context_builder = getattr(registry, "documentation_context", None) or build_doc_context_agent
    if grounding == GITHUB:
        context_repo_tools = _scope_read_only_tools(reg.github_intelligence)
    elif grounding == DOCS:
        context_repo_tools = []
    else:
        context_repo_tools = _scope_read_only_tools(reg.documentation_engineer)
    context_agent = context_builder(
        context_model,
        _dedupe(
            context_repo_tools,
            reg.semantic_search,
            reg.keyword_search,
            reg.hybrid_search,
            reg.live_docs_search,
        ),
        grounding=grounding,
        repo_dir=repo_dir,
        runtime=steering_runtime,
        agent_id="documentation.context",
        node_id="context",
    )
    research_builder = (
        getattr(registry, "documentation_research_swarm", None) or build_doc_research_swarm
    )
    if grounding == GITHUB:
        swarm_github_tools = _scope_read_only_tools(reg.github_intelligence)
        swarm_local_tools: list[Any] = []
    elif grounding == DOCS:
        swarm_github_tools = []
        swarm_local_tools = []
    else:
        swarm_github_tools = []
        swarm_local_tools = _dedupe(
            _scope_read_only_tools(reg.documentation_engineer),
            reg.semantic_search,
            reg.keyword_search,
            reg.hybrid_search,
            _LOCAL_CODE_SEARCH,
        )
    research_kwargs: dict[str, Any] = {
        "local_tools": swarm_local_tools,
        "github_tools": swarm_github_tools,
        "grounding": grounding,
        "repo_dir": repo_dir,
        "runtime": steering_runtime,
        "agent_id": "documentation.research",
        "node_id": "research",
        # Operator-configured Strands budgets: the same node_timeout /
        # execution_timeout the graph applies (STRANDS_NODE_TIMEOUT etc.),
        # overriding the swarm's hardcoded 300s/900s defaults so a research
        # agent doing heavy tooling is never killed twice as fast as the
        # graph node allows.
        "execution_timeout": execution_timeout,
        "node_timeout": node_timeout,
    }
    if research_plan is not None:
        research_kwargs["plan"] = research_plan
    research_swarm = research_builder(research_model, reg, **research_kwargs)
    impact_builder = getattr(registry, "impact_agent", None) or build_impact_agent
    if grounding == GITHUB:
        impact_repo_tools = _dedupe(
            _scope_read_only_tools(reg.github_intelligence),
            [t for t in reg.documentation if t is not code_search],
        )
    elif grounding == DOCS:
        impact_repo_tools = [t for t in reg.documentation if t is not code_search]
    else:
        impact_repo_tools = reg.documentation
    impact_agent = impact_builder(
        intelligence_model,
        _dedupe(
            reg.semantic_search,
            reg.keyword_search,
            reg.hybrid_search,
            reg.live_docs_search,
            impact_repo_tools,
        ),
        runtime=steering_runtime,
        agent_id="documentation.impact",
        node_id="impact",
    )
    answer_builder = getattr(registry, "answer_writer", None) or build_answer_writer
    answer_agent = answer_builder(
        support_model,
        _dedupe(reg.semantic_search, reg.keyword_search),
        runtime=steering_runtime,
        agent_id="documentation.answer",
        node_id="answer",
    )
    # Two DISTINCT instances: the SDK rejects duplicate executors.
    # Per-task routing: writer nodes resolve their own model when a
    # resolver is wired in; concrete/shared models pass through verbatim.
    writer_model = resolve_model_for_role(model, "documentation_engineer")
    writer_repo_tools = filter_grounded_tools(
        grounding, _scope_writer_tools(reg.documentation_engineer, reg.documentation)
    )
    if grounding == GITHUB:
        # PR runs have no local checkout: the writer must still read the docs
        # it updates, so give it the read-only GitHub-API repo tools. Without a
        # read path it looped calling unregistered read_file / list_files until
        # the graph killed it (run d7cfb2a0).
        # Research and impact already gathered diff/search evidence. Keep the
        # page writer focused on reading exact files at the pinned PR commit;
        # a broad search tool let one response request hundreds of searches.
        allowed_writer_reads = {
            "analyze_structure",
            "extract_frontmatter",
            "extract_links",
            "find_section",
            "generate_toc",
            "markdown_to_text",
            "split_sections",
            "validate_links",
            "github_read_file",
            "github_get_tree",
            # Legacy names the writer model reliably reaches for (run 67d19310):
            # registered so those calls execute instead of tripping the guard.
            "github_get_file",
            "github_list_tree",
        }
        writer_repo_tools = [
            tool
            for tool in _dedupe(writer_repo_tools, _scope_read_only_tools(reg.github_intelligence))
            if _tool_name(tool) in allowed_writer_reads
        ]
        writer_repo_tools = [*writer_repo_tools, github_get_file, github_list_tree]
    writer_tools = _dedupe(writer_repo_tools, [start_draft, append_chunk, finalize_draft])
    writer_builder = getattr(registry, "writer_agent", None) or build_writer_agent
    writer_factory = WriterFactory(
        model=writer_model,
        tools=writer_tools,
        runtime=steering_runtime,
        builder=writer_builder,
    )
    # Single write path: the page workflow yields a fresh writer Agent per task
    # (the SDK rejects duplicate executors, so writers are never pre-built).
    # Reviewer: one isolated agent per cross-page review invocation reads
    # compact per-page summaries and returns a ReviewVerdict (clean | correct +
    # targeted per-task corrections). Built lazily like the writer so the SDK
    # never sees a duplicate executor.
    reviewer_builder = getattr(registry, "review_agent", None) or build_review_agent

    def _reviewer_factory() -> Any:
        return reviewer_builder(
            reviewer_model,
            [],
            runtime=steering_runtime,
            agent_id="documentation.reviewer",
            node_id="review",
        )

    if page_workflow is not None:
        document_node = DocumentationWorkflowNode(
            "document",
            repository=page_workflow,
            handlers={
                "write": PageWriterHandler(
                    writer_factory=writer_factory,
                    drafts_repo=drafts_repo,
                    page_repository=page_workflow,
                    documents_repo=documents_repo,
                    limits=writer_limits,
                ),
                "evaluate": PageEvaluatorHandler(
                    rubric_grader=docs_rubric_grader,
                    drafts_repo=drafts_repo,
                    page_repository=page_workflow,
                ),
                "cross_page_review": CrossPageReviewHandler(
                    reviewer_factory=_reviewer_factory,
                    drafts_repo=drafts_repo,
                    page_repository=page_workflow,
                ),
            },
            write_concurrency=write_concurrency,
            progress_sink=progress_sink,
        )
    else:
        # Offline fixtures: no durable workflow. The node fails fast rather
        # than reporting phantom sealed pages.
        document_node = DocumentationWorkflowNode("document", repository=None, handlers=None)

    delivery_builder = getattr(registry, "delivery_agent", None) or build_delivery_agent
    delivery_agent = delivery_builder(
        delivery_model,
        _dedupe(
            reg.github_delivery,
            reg.slack_post_message,
            reg.discord_post_message,
            [get_drafted_docs],
        ),
        hitl=False,  # the graph-level ReviewGate owns human approval
        runtime=steering_runtime,
        agent_id="delivery.github",
        node_id="deliver",
    )
    changelog_builder = getattr(registry, "changelog_agent", None) or build_changelog_agent
    changelog_repo_tools = filter_grounded_tools(
        grounding, _scope_writer_tools(reg.documentation_engineer, reg.documentation)
    )
    if grounding == GITHUB:
        # Same PR-run read path as the writer: the changelog prompt asks for the
        # existing CHANGELOG.md, which is unreadable with the stripped local
        # tools — give it the GitHub-API repo tools.
        changelog_repo_tools = _dedupe(
            changelog_repo_tools, _scope_read_only_tools(reg.github_intelligence)
        )
    changelog_agent = changelog_builder(
        writer_model,
        changelog_repo_tools,
        runtime=steering_runtime,
        agent_id="documentation.changelog",
        node_id="changelog",
    )
    changelog_evaluator = ChangelogEvaluatorNode(
        "changelog_evaluate",
        max_iterations=evaluator_max_iterations,
        rubric_grader=changelog_rubric_grader,
    )
    answer_quality = AnswerQualityNode(
        "answer_evaluate",
        max_iterations=evaluator_max_iterations,
        rubric_grader=docs_rubric_grader,
    )

    builder = GraphBuilder()
    builder.set_graph_id(graph_id)

    # Intake / classification
    builder.add_node(classifier, "classify")
    builder.set_entry_point("classify")

    # Context
    from draftly.agents.shared.memory_grounding import MemoryGroundedNode

    # Always wrapped, memory or not. Strands takes `limits` per invocation and
    # its Graph forwards none, so without this the context agent's loop is
    # unbounded (run ce8ea540). ``MemoryGroundedNode`` is the carrier rather
    # than a dedicated wrapper because it already performs the
    # ``AgentResult`` -> ``MultiAgentResult`` conversion the graph requires on
    # its MultiAgentBase branch; a wrapper returning the inner result verbatim
    # leaves the graph reading ``execution_time`` off an ``AgentResult``.
    # ``memory=None`` passes the task through unchanged.
    context_node = MemoryGroundedNode(context_agent, memory, limits=context_limits)
    builder.add_node(context_node, "context")
    builder.add_edge("classify", "context", condition=is_valid_surface)

    # Research
    builder.add_node(research_swarm, "research")
    builder.add_edge("context", "research")

    research_failed = bool(research_plan is not None and getattr(research_plan, "failure", None))

    # Impact analysis. When research fails fast (missing mandatory capability)
    # the research edge is omitted: impact then has no in-edges and is never
    # scheduled, so the graph terminates with the failed research node
    # (FAILED via failed_nodes) and impact/writers/delivery are not invoked.
    builder.add_node(impact_agent, "impact")
    if not research_failed:
        builder.add_edge("research", "impact")

    # Generation (mutually exclusive conditions)
    builder.add_node(answer_agent, "answer")
    builder.add_node(document_node, "document")
    builder.add_edge("impact", "answer", condition=route_to_answer)
    builder.add_edge("impact", "document", condition=route_to_write)
    # The research evidence feeds the page workflow's deterministic planner
    # (scoped evidence per task path). Gated like the impact edge so the work
    # is only ever scheduled on a write.
    builder.add_edge("research", "document", condition=route_to_write)
    # The context node is the only node that emits a structured EvidenceBundle
    # (research is a text swarm, so parse_node_input drops it). Without this
    # edge its items reach no one and every page escalates for missing
    # evidence. Gated like the other content edges.
    builder.add_edge("context", "document", condition=route_to_write)

    # PR notify: post the impact result directly so the comment cannot drift
    # from the actual page plan. Runs only for pull_request.opened events.
    notify_post = NotifyPostNode(
        "notify_post",
        comment_factory=comment_factory,
    )
    builder.add_node(notify_post, "notify_post")
    builder.add_edge("impact", "notify_post", condition=pull_request_opened)

    # Answer quality gate with a bounded revision loop. The unconditional
    # answer edge schedules the gate; research supplies the evidence the
    # deterministic score needs (gated so it cannot fire before a write routes).
    builder.add_node(answer_quality, "answer_evaluate")
    builder.add_edge("answer", "answer_evaluate")
    builder.add_edge("research", "answer_evaluate", condition=route_to_answer)
    builder.add_edge("answer_evaluate", "answer", condition=answer_needs_revision)

    # Delivery (the graph-level ReviewGate intercepts before invocation)
    builder.add_node(delivery_agent, "deliver")

    # Changelog generation (runs for every release event)
    builder.add_node(changelog_agent, "changelog")
    builder.add_node(changelog_evaluator, "changelog_evaluate")

    # Normal paths: docs/answer evaluated → changelog → changelog evaluated →
    # deliver. The answer path generates the changelog via its own quality
    # gate (matching the legacy ``evaluate → changelog`` behavior); the docs
    # path only releases a changelog when every page passed (escalated runs
    # skip the changelog and route the change to the human gate directly).
    builder.add_edge("document", "changelog", condition=documentation_passed)
    builder.add_edge("answer_evaluate", "changelog", condition=answer_eval_passed)

    # No-docs release path: impact none + release → changelog (skips writer/evaluate)
    builder.add_edge("impact", "changelog", condition=none_and_release)

    # Changelog revision loop
    builder.add_edge("changelog", "changelog_evaluate", condition=generated_changelog)
    builder.add_edge("changelog_evaluate", "changelog", condition=changelog_needs_revision)

    # Changelog passes → deliver. Delivery content edges (document/answer/
    # changelog → deliver below) only feed the prompt; this edge schedules the
    # node and is therefore also the docs gate: when the page workflow ran,
    # delivery waits for a settled pass (documentation_passed) so the deliver
    # agent never opens a PR for pages that never sealed.
    builder.add_edge("changelog_evaluate", "deliver", condition=delivery_content_ready)

    # Delivery content edges: the deliver prompt is built from the outputs of
    # nodes with a directed edge into it (see Graph._build_node_input). Without
    # these edges the approved page workflow result and changelog markdown
    # never reach the delivery agent. They are gated so scheduling (which fires
    # on ANY freshly-satisfied in-edge) cannot trigger deliver early — each
    # edge is False when its writer/changelog completes, and only True once the
    # changelog gate has passed or the page workflow escalated to human
    # review, when the real scheduling edges run the node.
    builder.add_edge("document", "deliver", condition=documentation_delivery_ready)
    builder.add_edge("answer", "deliver", condition=delivery_content_ready)
    builder.add_edge("changelog", "deliver", condition=delivery_content_ready)

    # Safety rails
    builder.set_max_node_executions(max_node_executions)
    builder.set_execution_timeout(execution_timeout)
    builder.set_node_timeout(node_timeout)
    builder.reset_on_revisit(True)

    # Session persistence + hooks (providers MUST be set pre-build)
    if session_manager is not None:
        builder.set_session_manager(session_manager)
    providers: list[Any] = [ReviewGate(), NextGenerationHook()]
    audit_hook: Any = None
    if audit_repo is not None or publisher is not None or jobs_repo is not None:
        audit_hook = RunAuditLogger(audit_repo, publisher=publisher, jobs_repo=jobs_repo)
        providers.append(audit_hook)
    if hooks:
        providers.extend(hooks)
    builder.set_hook_providers(providers)
    graph = builder.build()
    if audit_hook is not None:
        graph._draftly_audit_hook = audit_hook
    return graph
