"""Support graph (Slack/Discord): classify → context → research → impact →
answer (or update/create when the question reveals a documentation gap) →
evaluate → review → deliver (reply in thread). The question is recorded for
the feedback loop by the runner via the support event store.
"""

from __future__ import annotations

import logging
from typing import Any

from strands.multiagent import GraphBuilder
from strands.session.session_manager import SessionManager

from draftly.orchestration.graphs.documentation_graph import (
    DEFAULT_EVALUATOR_MAX_ITERATIONS,
    DEFAULT_EXECUTION_TIMEOUT,
    DEFAULT_MAX_NODE_EXECUTIONS,
    DEFAULT_NODE_TIMEOUT,
    _dedupe,
)
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

logger = logging.getLogger(__name__)

SUPPORT_GRAPH_ID = "draftly-support-graph"


def build_support_graph(
    session_manager: SessionManager | None,
    tools_registry: Any,
    model: Any,
    hooks: list[Any] | None = None,
    *,
    graph_id: str = SUPPORT_GRAPH_ID,
    audit_repo: Any = None,
    memory: Any = None,
    max_node_executions: int = DEFAULT_MAX_NODE_EXECUTIONS,
    execution_timeout: float = DEFAULT_EXECUTION_TIMEOUT,
    node_timeout: float = DEFAULT_NODE_TIMEOUT,
    evaluator_max_iterations: int = DEFAULT_EVALUATOR_MAX_ITERATIONS,
):
    """Build the Slack/Discord support surface graph."""
    from draftly.agents.documentation.writer import build_writer_agent
    from draftly.agents.shared.classifier import build_classifier
    from draftly.agents.shared.context import build_context_agent
    from draftly.agents.shared.delivery import build_delivery_agent
    from draftly.agents.subagents import build_research_swarm
    from draftly.agents.support.answer_writer import build_answer_writer
    from draftly.agents.support.question_analyzer import (
        build_question_analyzer,
    )
    from draftly.agents.support.solution_researcher import (
        build_solution_researcher,
    )

    reg = tools_registry

    classifier = build_classifier(model)
    context_agent = build_context_agent(
        model,
        _dedupe(
            reg.semantic_search,
            reg.keyword_search,
            reg.slack_search,
            reg.slack_get_thread,
            reg.discord_search,
            reg.discord_get_thread,
        ),
    )
    research_swarm = build_research_swarm(model, reg)
    question_analyzer = build_question_analyzer(
        model,
        _dedupe(reg.semantic_search),
    )
    solution_researcher = build_solution_researcher(
        model,
        _dedupe(
            reg.semantic_search,
            reg.keyword_search,
            reg.github_intelligence,
        ),
    )
    answer_agent = build_answer_writer(
        model,
        _dedupe(reg.semantic_search, reg.keyword_search),
    )
    update_writer = build_writer_agent(
        model,
        _dedupe(reg.documentation_engineer, reg.documentation),
    )
    create_writer = build_writer_agent(
        model,
        _dedupe(reg.documentation_engineer, reg.documentation),
    )
    delivery_agent = build_delivery_agent(
        model,
        _dedupe(reg.slack_post_message, reg.discord_post_message),
        hitl=False,  # the graph-level ReviewGate owns human approval
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

    evaluator = EvaluatorNode(
        "evaluate", max_iterations=evaluator_max_iterations
    )
    builder.add_node(evaluator, "evaluate")
    builder.add_edge("answer", "evaluate", condition=generated)
    builder.add_edge("update", "evaluate", condition=generated)
    builder.add_edge("create", "evaluate", condition=generated)

    builder.add_edge("evaluate", "update", condition=needs_revision_of("update"))
    builder.add_edge("evaluate", "create", condition=needs_revision_of("create"))

    builder.add_node(delivery_agent, "deliver")
    builder.add_edge("evaluate", "deliver", condition=eval_passed)

    builder.set_max_node_executions(max_node_executions)
    builder.set_execution_timeout(execution_timeout)
    builder.set_node_timeout(node_timeout)
    builder.reset_on_revisit(True)

    builder.set_session_manager(session_manager)
    providers: list[Any] = [ReviewGate()]
    if audit_repo is not None:
        from draftly.orchestration.hooks.audit import RunAuditLogger

        providers.append(RunAuditLogger(audit_repo))
    if hooks:
        providers.extend(hooks)
    builder.set_hook_providers(providers)

    return builder.build()
