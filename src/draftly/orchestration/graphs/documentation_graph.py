"""The unified Draftly documentation graph.

Layout (plan §6.1)::

    classify → context → research(Swarm) → impact
      ├─(answer)─► answer ─┐
      ├─(update)─► update ─┤
      └─(create)─► create ─┴─► evaluate ─(passed)──► deliver
                                  │▲
                                  └─(needs_revision_of)─┘

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

import logging
from typing import Any

from strands.multiagent import GraphBuilder
from strands.session.session_manager import SessionManager

from draftly.orchestration.hooks.audit import RunAuditLogger
from draftly.orchestration.hooks.review_gate import ReviewGate
from draftly.orchestration.nodes.evaluate import EvaluatorNode
from draftly.orchestration.routing.conditions import (
    eval_passed,
    generated,
    is_valid_surface,
    needs_revision_of,
    route_to_answer,
    route_to_create,
    route_to_update,
)

logger = logging.getLogger(__name__)

DEFAULT_GRAPH_ID = "draftly-main-graph"
DEFAULT_MAX_NODE_EXECUTIONS = 10
DEFAULT_EXECUTION_TIMEOUT = 600.0
DEFAULT_NODE_TIMEOUT = 180.0
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
    graph_id: str = DEFAULT_GRAPH_ID,
    audit_repo: Any = None,
    memory: Any = None,
    max_node_executions: int = DEFAULT_MAX_NODE_EXECUTIONS,
    execution_timeout: float = DEFAULT_EXECUTION_TIMEOUT,
    node_timeout: float = DEFAULT_NODE_TIMEOUT,
    evaluator_max_iterations: int = DEFAULT_EVALUATOR_MAX_ITERATIONS,
):
    """Build the unified Draftly Graph for documentation workflows."""
    # Import agents (factories — one instance per graph node)
    from draftly.agents.documentation.analyzer import build_impact_agent
    from draftly.agents.documentation.writer import build_writer_agent
    from draftly.agents.shared.classifier import build_classifier
    from draftly.agents.shared.context import build_context_agent
    from draftly.agents.shared.delivery import build_delivery_agent
    from draftly.agents.subagents import build_research_swarm
    from draftly.agents.support.answer_writer import build_answer_writer

    reg = tools_registry

    classifier = build_classifier(model)
    context_agent = build_context_agent(
        model,
        _dedupe(
            reg.github_intelligence,
            reg.semantic_search,
            reg.keyword_search,
            reg.hybrid_search,
            reg.slack_search,
            reg.slack_get_thread,
            reg.discord_search,
            reg.discord_get_thread,
        ),
    )
    research_swarm = build_research_swarm(model, reg)
    impact_agent = build_impact_agent(
        model,
        _dedupe(
            reg.semantic_search,
            reg.keyword_search,
            reg.hybrid_search,
            reg.documentation,
        ),
    )
    answer_agent = build_answer_writer(
        model,
        _dedupe(reg.semantic_search, reg.keyword_search),
    )
    # Two DISTINCT instances: the SDK rejects duplicate executors.
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
        _dedupe(reg.github_delivery, reg.slack_post_message, reg.discord_post_message),
        hitl=False,  # the graph-level ReviewGate owns human approval
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

    # Evaluation
    evaluator = EvaluatorNode(
        "evaluate", max_iterations=evaluator_max_iterations
    )
    builder.add_node(evaluator, "evaluate")
    builder.add_edge("answer", "evaluate", condition=generated)
    builder.add_edge("update", "evaluate", condition=generated)
    builder.add_edge("create", "evaluate", condition=generated)

    # Revise loop — scoped to whichever generation node actually ran
    builder.add_edge("evaluate", "update", condition=needs_revision_of("update"))
    builder.add_edge("evaluate", "create", condition=needs_revision_of("create"))

    # Delivery
    builder.add_node(delivery_agent, "deliver")
    builder.add_edge("evaluate", "deliver", condition=eval_passed)

    # Safety rails
    builder.set_max_node_executions(max_node_executions)
    builder.set_execution_timeout(execution_timeout)
    builder.set_node_timeout(node_timeout)
    builder.reset_on_revisit(True)

    # Session persistence + hooks (providers MUST be set pre-build)
    builder.set_session_manager(session_manager)
    providers: list[Any] = [ReviewGate()]
    if audit_repo is not None:
        providers.append(RunAuditLogger(audit_repo))
    if hooks:
        providers.extend(hooks)
    builder.set_hook_providers(providers)

    return builder.build()
