"""Support graph (Slack/Discord): classify → context → research → impact →
answer (or update/create when the question reveals a documentation gap) →
evaluate → review → deliver (reply in thread). The question is recorded for
the feedback loop by the runner via the support event store.
"""

from __future__ import annotations

from typing import Any

import structlog
from strands.multiagent import GraphBuilder
from strands.session.session_manager import SessionManager

from draftly.orchestration.graphs.documentation_graph import (
    DEFAULT_EVALUATOR_MAX_ITERATIONS,
    DEFAULT_EXECUTION_TIMEOUT,
    DEFAULT_MAX_NODE_EXECUTIONS,
    DEFAULT_NODE_TIMEOUT,
    _dedupe,
)
from draftly.orchestration.graphs.tool_scoping import scope_writer_tools
from draftly.orchestration.hooks.review_gate import ReviewGate
from draftly.orchestration.nodes.evaluate import EvaluatorNode
from draftly.orchestration.routing.conditions import (
    eval_passed,
    generated,
    is_valid_surface,
    needs_revision_of,
    route_to_answer_of,
    route_to_create_of,
    route_to_update_of,
)

logger = structlog.get_logger(__name__)

SUPPORT_GRAPH_ID = "draftly-support-graph"


def _delivery_tools_for_source(source: str | None, registry: Any) -> list[Any]:
    """Scope the support delivery agent to the origin platform's tools.

    Direct answers return to the originating thread, so the model must never
    be handed a cross-platform poster: a Slack run sees only
    ``slack_post_message`` and a Discord run only ``discord_post_message``.
    ``github`` scopes to the documentation-delivery tools for a typed gap
    handoff. Unknown sources (supervised builds without a runtime) fall back
    to the union of platform posting tools.
    """
    if source == "slack":
        return list(getattr(registry, "slack_post_message", None) or [])
    if source == "discord":
        return list(getattr(registry, "discord_post_message", None) or [])
    if source == "github":
        return list(getattr(registry, "github_delivery", None) or [])
    return list(
        _dedupe(
            getattr(registry, "slack_post_message", None),
            getattr(registry, "discord_post_message", None),
        )
    )


def build_support_graph(
    session_manager: SessionManager | None,
    tools_registry: Any,
    model: Any,
    hooks: list[Any] | None = None,
    *,
    agents: Any = None,
    graph_id: str = SUPPORT_GRAPH_ID,
    audit_repo: Any = None,
    memory: Any = None,
    publisher: Any = None,
    jobs_repo: Any = None,
    source: str | None = None,
    max_node_executions: int = DEFAULT_MAX_NODE_EXECUTIONS,
    execution_timeout: float = DEFAULT_EXECUTION_TIMEOUT,
    node_timeout: float = DEFAULT_NODE_TIMEOUT,
    evaluator_max_iterations: int = DEFAULT_EVALUATOR_MAX_ITERATIONS,
):
    """Build the Slack/Discord support surface graph.

    ``source`` is the normalized event platform (``slack`` or ``discord``)
    when it is known at build time; otherwise it is resolved from the active
    support runtime so delivery is scoped to the originating platform.
    """
    from draftly.agents.documentation.writer import build_writer_agent
    from draftly.agents.shared.classifier import build_classifier
    from draftly.agents.shared.context import build_context_agent
    from draftly.agents.shared.delivery import build_delivery_agent
    from draftly.agents.support.answer_writer import build_answer_writer
    from draftly.agents.support.question_analyzer import (
        build_question_analyzer,
    )
    from draftly.agents.support.research_swarm import build_support_research_swarm
    from draftly.agents.support.solution_researcher import (
        build_solution_researcher,
    )
    from draftly.integrations.strands.models import resolve_model_for_role
    from draftly.integrations.support.runtime import current_support_runtime
    from draftly.tools.repository.code_search import code_search

    reg = tools_registry

    runtime = current_support_runtime()
    source = source or (runtime.platform if runtime is not None else None)

    classifier_model = resolve_model_for_role(model, "classifier")
    context_model = resolve_model_for_role(model, "context")
    support_model = resolve_model_for_role(model, "support_engineer")
    research_model = resolve_model_for_role(model, "research")
    writer_model = resolve_model_for_role(model, "documentation_engineer")
    delivery_model = resolve_model_for_role(model, "github_delivery")

    registry = agents
    classifier_builder = getattr(registry, "classifier", None) or build_classifier
    classifier = classifier_builder(classifier_model)
    context_builder = getattr(registry, "context_agent", None) or build_context_agent
    context_agent = context_builder(
        context_model,
        _dedupe(
            reg.semantic_search,
            reg.keyword_search,
            reg.slack_search,
            reg.slack_get_thread,
            reg.discord_search,
            reg.discord_get_thread,
        ),
    )
    research_builder = (
        getattr(registry, "support_research_swarm", None) or build_support_research_swarm
    )
    research_swarm = research_builder(
        research_model,
        reg,
        local_tools=_dedupe(
            reg.semantic_search,
            reg.keyword_search,
            [code_search],
        ),
    )
    question_builder = getattr(registry, "question_analyzer", None) or build_question_analyzer
    question_analyzer = question_builder(
        support_model,
        _dedupe(reg.semantic_search, reg.keyword_search),
    )
    solution_builder = getattr(registry, "solution_researcher", None) or build_solution_researcher
    solution_researcher = solution_builder(
        support_model,
        _dedupe(
            reg.semantic_search,
            reg.keyword_search,
            [code_search],
        ),
    )
    answer_builder = getattr(registry, "answer_writer", None) or build_answer_writer
    answer_agent = answer_builder(
        support_model,
        _dedupe(reg.semantic_search, reg.keyword_search),
    )
    writer_builder = getattr(registry, "writer_agent", None) or build_writer_agent
    update_writer = writer_builder(
        writer_model,
        scope_writer_tools(reg.documentation_engineer, reg.documentation),
    )
    create_writer = writer_builder(
        writer_model,
        scope_writer_tools(reg.documentation_engineer, reg.documentation),
    )
    delivery_builder = getattr(registry, "delivery_agent", None) or build_delivery_agent
    delivery_agent = delivery_builder(
        delivery_model,
        _delivery_tools_for_source(source, reg),
        hitl=False,  # the graph-level ReviewGate owns human approval
        skill_names=("support-delivery",),
    )

    builder = GraphBuilder()
    builder.set_graph_id(graph_id)

    builder.add_node(classifier, "classify")
    builder.set_entry_point("classify")

    context_node = context_agent
    if memory is not None:
        from draftly.agents.shared.memory_grounding import MemoryGroundedNode

        context_node = MemoryGroundedNode(context_agent, memory)
    builder.add_node(context_node, "context")
    builder.add_edge("classify", "context", condition=is_valid_surface)

    builder.add_node(research_swarm, "research")
    builder.add_edge("context", "research")

    builder.add_node(question_analyzer, "triage")
    builder.add_edge("research", "triage")

    builder.add_node(solution_researcher, "impact")
    builder.add_edge("triage", "impact")

    builder.add_node(answer_agent, "answer")
    builder.add_node(update_writer, "update")
    builder.add_node(create_writer, "create")
    # Routing keys off the triage node's ImpactAnalysis — the impact node
    # (solution researcher) produces free-text research, not a verdict.
    builder.add_edge("impact", "answer", condition=route_to_answer_of("triage"))
    builder.add_edge("impact", "update", condition=route_to_update_of("triage"))
    builder.add_edge("impact", "create", condition=route_to_create_of("triage"))

    evaluator = EvaluatorNode("evaluate", max_iterations=evaluator_max_iterations)
    builder.add_node(evaluator, "evaluate")
    builder.add_edge("answer", "evaluate", condition=generated)
    builder.add_edge("update", "evaluate", condition=generated)
    builder.add_edge("create", "evaluate", condition=generated)
    builder.add_edge("context", "evaluate", condition=generated)
    builder.add_edge("research", "evaluate", condition=generated)

    builder.add_edge("evaluate", "answer", condition=needs_revision_of("answer"))
    builder.add_edge("evaluate", "update", condition=needs_revision_of("update"))
    builder.add_edge("evaluate", "create", condition=needs_revision_of("create"))

    builder.add_node(delivery_agent, "deliver")
    builder.add_edge("evaluate", "deliver", condition=eval_passed)

    builder.set_max_node_executions(max_node_executions)
    builder.set_execution_timeout(execution_timeout)
    builder.set_node_timeout(node_timeout)
    builder.reset_on_revisit(True)

    if session_manager is not None:
        builder.set_session_manager(session_manager)
    providers: list[Any] = [ReviewGate()]
    if audit_repo is not None or publisher is not None or jobs_repo is not None:
        from draftly.orchestration.hooks.audit import RunAuditLogger

        providers.append(RunAuditLogger(audit_repo, publisher=publisher, jobs_repo=jobs_repo))
    if hooks:
        providers.extend(hooks)
    builder.set_hook_providers(providers)

    return builder.build()
