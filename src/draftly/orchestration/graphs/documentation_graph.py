"""The unified Draftly documentation graph.

Layout (plan §6.1)::

    classify → context → research(Swarm) → impact
      ├─(answer)─► answer ─┐
      ├─(update)─► update ─┤
      └─(create)─► create ─┴─► evaluate ─(passed)──► deliver
                                  │▲
                                  └─(needs_revision_of)─┘
    impact ─(pull_request_opened)─► notify ─► notify_post   (parallel branch)

Deviations from the plan, forced by strands-agents 1.52.0 behavior:

- Agent instances must be unique per node (``_validate_graph`` raises on
  duplicates), so the writer agent is built twice for ``update``/``create``.
- Hook providers attach via ``GraphBuilder.set_hook_providers`` before
  ``build()``; ``Graph.add_hook`` only accepts bare callbacks.
- Revise edges use ``needs_revision_of(node_id)`` — a shared
  ``needs_revision`` condition would fire BOTH revise edges at once.
- ``EvaluatorNode`` defaults to ``max_iterations=2``: with the plan's 3,
  the worst case (4 upstream + 3×(generate+evaluate) + deliver = 11)
  exceeds ``max_node_executions=10`` and delivery would never run.
"""

from __future__ import annotations

from typing import Any

import structlog
from strands.multiagent import GraphBuilder
from strands.session.session_manager import SessionManager

from draftly.evaluation.evaluators.completeness import COMPLETENESS_RUBRIC
from draftly.evaluation.evaluators.groundedness import GROUNDEDNESS_RUBRIC
from draftly.integrations.strands.models import resolve_model_for_role
from draftly.orchestration.graphs.tool_scoping import (
    scope_read_only_tools as _scope_read_only_tools,
)
from draftly.orchestration.graphs.tool_scoping import (
    scope_writer_tools as _scope_writer_tools,
)
from draftly.orchestration.hooks.audit import RunAuditLogger
from draftly.orchestration.hooks.review_gate import ReviewGate
from draftly.orchestration.nodes.evaluate import EvaluatorNode
from draftly.orchestration.nodes.rubric_grader import (
    build_changelog_rubric_grader,
    build_docs_rubric_grader,
)
from draftly.orchestration.routing.conditions import (
    changelog_eval_passed,
    changelog_needs_revision,
    eval_passed,
    generated,
    generated_changelog,
    is_valid_surface,
    needs_revision_of,
    none_and_release,
    pull_request_opened,
    route_to_answer,
    route_to_create,
    route_to_update,
)
from draftly.tools.repository.code_search import code_search
from draftly.workflows.grounding import DOCS, GITHUB, LOCAL

logger = structlog.get_logger(__name__)

# Selected repository tools for the docs graph's local-first researcher. Avoids
# the GitHub API tools (get_pull_request/get_files/get_diff) that would 401 in
# the offline evaluation harness; the workspace is a local authly worktree.
_LOCAL_CODE_SEARCH = [code_search]

DEFAULT_GRAPH_ID = "draftly-main-graph"
DEFAULT_MAX_NODE_EXECUTIONS = 15
# 600s killed delivered PR runs at the tail (8+ sequential LLM nodes plus a
# datadog-style revision loop); keep enough ceiling to reach the ReviewGate.
DEFAULT_EXECUTION_TIMEOUT = 1800.0
DEFAULT_NODE_TIMEOUT = 600.0
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
    """
    # Import agents (factories — one instance per graph node)
    from draftly.agents.documentation.analyzer import build_impact_agent
    from draftly.agents.documentation.changelog import build_changelog_agent
    from draftly.agents.documentation.context import build_doc_context_agent
    from draftly.agents.documentation.research_swarm import build_doc_research_swarm
    from draftly.agents.documentation.writer import build_writer_agent
    from draftly.agents.notify import build_notify_agent
    from draftly.agents.shared.classifier import build_classifier
    from draftly.agents.shared.delivery import build_delivery_agent
    from draftly.agents.support.answer_writer import build_answer_writer
    from draftly.orchestration.nodes.changelog_evaluate import ChangelogEvaluatorNode
    from draftly.orchestration.nodes.notify_post import NotifyPostNode

    reg = tools_registry

    classifier_model = resolve_model_for_role(model, "classifier")
    context_model = resolve_model_for_role(model, "context")
    support_model = resolve_model_for_role(model, "support_engineer")
    research_model = resolve_model_for_role(model, "research")
    intelligence_model = resolve_model_for_role(model, "github_intelligence")
    delivery_model = resolve_model_for_role(model, "github_delivery")
    grader_model = resolve_model_for_role(model, "documentation_reviewer")

    # Mandatory rubric graders: LLM feedback enriches the deterministic docs
    # and changelog gates on every failed draft (the future). Built eagerly
    # from the resolved reviewer model; OutputEvaluator is lazy so this is
    # safe offline.
    docs_rubric_grader = build_docs_rubric_grader(
        grader_model, rubric=GROUNDEDNESS_RUBRIC + "\n\n" + COMPLETENESS_RUBRIC
    )
    changelog_rubric_grader = build_changelog_rubric_grader(grader_model)

    registry = agents
    classifier = (getattr(registry, "classifier", None) or build_classifier)(classifier_model)
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
            reg.slack_search,
            reg.slack_get_thread,
            reg.discord_search,
            reg.discord_get_thread,
        ),
        grounding=grounding,
        repo_dir=repo_dir,
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
    research_swarm = research_builder(
        research_model,
        reg,
        local_tools=swarm_local_tools,
        github_tools=swarm_github_tools,
        grounding=grounding,
        repo_dir=repo_dir,
    )
    impact_builder = getattr(registry, "impact_agent", None) or build_impact_agent
    impact_agent = impact_builder(
        intelligence_model,
        _dedupe(
            reg.semantic_search,
            reg.keyword_search,
            reg.hybrid_search,
            reg.documentation,
        ),
    )
    notify_model = resolve_model_for_role(model, "notify")
    notify_builder = getattr(registry, "notify_agent", None) or build_notify_agent
    notify_agent = notify_builder(notify_model, [])
    answer_builder = getattr(registry, "answer_writer", None) or build_answer_writer
    answer_agent = answer_builder(
        support_model,
        _dedupe(reg.semantic_search, reg.keyword_search),
    )
    # Two DISTINCT instances: the SDK rejects duplicate executors.
    # Per-task routing: writer nodes resolve their own model when a
    # resolver is wired in; concrete/shared models pass through verbatim.
    writer_model = resolve_model_for_role(model, "documentation_engineer")
    writer_tools = _scope_writer_tools(reg.documentation_engineer, reg.documentation)
    writer_builder = getattr(registry, "writer_agent", None) or build_writer_agent
    update_writer = writer_builder(writer_model, writer_tools)
    create_writer = writer_builder(writer_model, writer_tools)
    delivery_builder = getattr(registry, "delivery_agent", None) or build_delivery_agent
    delivery_agent = delivery_builder(
        delivery_model,
        _dedupe(reg.github_delivery, reg.slack_post_message, reg.discord_post_message),
        hitl=False,  # the graph-level ReviewGate owns human approval
    )
    changelog_builder = getattr(registry, "changelog_agent", None) or build_changelog_agent
    changelog_agent = changelog_builder(
        writer_model,
        _scope_writer_tools(reg.documentation_engineer, reg.documentation),
    )
    changelog_evaluator = ChangelogEvaluatorNode(
        "changelog_evaluate",
        max_iterations=evaluator_max_iterations,
        rubric_grader=changelog_rubric_grader,
    )

    builder = GraphBuilder()
    builder.set_graph_id(graph_id)

    # Intake / classification
    builder.add_node(classifier, "classify")
    builder.set_entry_point("classify")

    # Context
    context_node = context_agent
    if memory is not None:
        from draftly.agents.shared.memory_grounding import MemoryGroundedNode

        context_node = MemoryGroundedNode(context_agent, memory)
    builder.add_node(context_node, "context")
    builder.add_edge("classify", "context", condition=is_valid_surface)

    # Research
    builder.add_node(research_swarm, "research")
    builder.add_edge("context", "research")

    # Impact analysis
    builder.add_node(impact_agent, "impact")
    builder.add_edge("research", "impact")

    # Generation fan-out (mutually exclusive conditions)
    builder.add_node(answer_agent, "answer")
    builder.add_node(update_writer, "update")
    builder.add_node(create_writer, "create")
    builder.add_edge("impact", "answer", condition=route_to_answer)
    builder.add_edge("impact", "update", condition=route_to_update)
    builder.add_edge("impact", "create", condition=route_to_create)

    # PR notify: a parallel branch off impact. The notify LLM composes the
    # comment (draft-only, no tools); the deterministic notify_post node posts
    # it. Runs for pull_request.opened events only — releases that route to
    # this graph are excluded by the condition.
    notify_post = NotifyPostNode(
        "notify_post",
        comment_factory=comment_factory,
    )
    builder.add_node(notify_agent, "notify")
    builder.add_node(notify_post, "notify_post")
    builder.add_edge("impact", "notify", condition=pull_request_opened)
    builder.add_edge("notify", "notify_post")

    # Evaluation
    evaluator = EvaluatorNode(
        "evaluate",
        max_iterations=evaluator_max_iterations,
        rubric_grader=docs_rubric_grader,
    )
    builder.add_node(evaluator, "evaluate")
    builder.add_edge("answer", "evaluate", condition=generated)
    builder.add_edge("update", "evaluate", condition=generated)
    builder.add_edge("create", "evaluate", condition=generated)
    # The writer edge makes evaluation ready; this conditional edge also
    # supplies the completed research payload as an evaluator dependency.
    # It is false when research completes (before a writer exists), so it
    # cannot trigger evaluation prematurely.
    builder.add_edge("context", "evaluate", condition=generated)
    builder.add_edge("research", "evaluate", condition=generated)

    # Revise loop — scoped to whichever generation node actually ran
    builder.add_edge("evaluate", "answer", condition=needs_revision_of("answer"))
    builder.add_edge("evaluate", "update", condition=needs_revision_of("update"))
    builder.add_edge("evaluate", "create", condition=needs_revision_of("create"))

    # Delivery
    builder.add_node(delivery_agent, "deliver")

    # Changelog generation (runs for every release event)
    builder.add_node(changelog_agent, "changelog")
    builder.add_node(changelog_evaluator, "changelog_evaluate")

    # Normal path: docs evaluated → changelog → changelog evaluated → deliver
    builder.add_edge("evaluate", "changelog", condition=eval_passed)
    builder.add_edge("changelog", "changelog_evaluate", condition=generated_changelog)

    # No-docs release path: impact none + release → changelog (skips writer/evaluate)
    builder.add_edge("impact", "changelog", condition=none_and_release)

    # Changelog revision loop
    builder.add_edge("changelog_evaluate", "changelog", condition=changelog_needs_revision)

    # Changelog passes → deliver
    builder.add_edge("changelog_evaluate", "deliver", condition=changelog_eval_passed)

    # Safety rails
    builder.set_max_node_executions(max_node_executions)
    builder.set_execution_timeout(execution_timeout)
    builder.set_node_timeout(node_timeout)
    builder.reset_on_revisit(True)

    # Session persistence + hooks (providers MUST be set pre-build)
    if session_manager is not None:
        builder.set_session_manager(session_manager)
    providers: list[Any] = [ReviewGate()]
    if audit_repo is not None or publisher is not None or jobs_repo is not None:
        providers.append(RunAuditLogger(audit_repo, publisher=publisher, jobs_repo=jobs_repo))
    if hooks:
        providers.extend(hooks)
    builder.set_hook_providers(providers)

    return builder.build()
