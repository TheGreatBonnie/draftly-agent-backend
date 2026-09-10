"""GitHub issue graph: classify → context → research → impact → answer
(or update/create when the issue reveals a documentation gap) → evaluate →
review → deliver (reply on the issue).
"""

from __future__ import annotations

from typing import Any

import structlog
from strands.multiagent import GraphBuilder
from strands.session.session_manager import SessionManager

from draftly.evaluation.evaluators.completeness import COMPLETENESS_RUBRIC
from draftly.evaluation.evaluators.groundedness import GROUNDEDNESS_RUBRIC
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
from draftly.orchestration.nodes.rubric_grader import build_docs_rubric_grader
from draftly.orchestration.routing.conditions import (
    eval_passed,
    generated,
    is_valid_surface,
    needs_revision_of,
    route_to_answer,
    route_to_create,
    route_to_update,
)
from draftly.tools.repository.code_search import code_search

logger = structlog.get_logger(__name__)

ISSUE_GRAPH_ID = "draftly-issue-graph"

# Local-repo code search for the issue surface's context/research/impact nodes.
# Mirrors the docs graph: online evaluation backs cases with a local authly
# checkout and no usable GitHub API, so analysis nodes inspect the checkout
# with code_search/semantic_search/keyword_search instead of 401-ing on
# get_issue. Delivery (issue_responder) keeps github_intelligence so
# production issue replies (create_comment) still work.
_LOCAL_CODE_SEARCH = [code_search]


def build_issue_graph(
    session_manager: SessionManager | None,
    tools_registry: Any,
    model: Any,
    hooks: list[Any] | None = None,
    *,
    agents: Any = None,
    graph_id: str = ISSUE_GRAPH_ID,
    audit_repo: Any = None,
    memory: Any = None,
    publisher: Any = None,
    jobs_repo: Any = None,
    max_node_executions: int = DEFAULT_MAX_NODE_EXECUTIONS,
    execution_timeout: float = DEFAULT_EXECUTION_TIMEOUT,
    node_timeout: float = DEFAULT_NODE_TIMEOUT,
    evaluator_max_iterations: int = DEFAULT_EVALUATOR_MAX_ITERATIONS,
    steering_runtime: Any = None,
):
    """Build the GitHub issue surface graph."""
    from draftly.agents.documentation.writer import build_writer_agent
    from draftly.agents.github.context import build_issue_context_agent
    from draftly.agents.github.issue_analyzer import build_issue_analyzer
    from draftly.agents.github.issue_responder import build_issue_responder
    from draftly.agents.github.research_swarm import build_issue_research_swarm
    from draftly.agents.shared.classifier import build_classifier
    from draftly.agents.support.answer_writer import build_answer_writer
    from draftly.integrations.strands.models import resolve_model_for_role

    reg = tools_registry

    classifier_model = resolve_model_for_role(model, "classifier")
    context_model = resolve_model_for_role(model, "context")
    support_model = resolve_model_for_role(model, "support_engineer")
    research_model = resolve_model_for_role(model, "research")
    writer_model = resolve_model_for_role(model, "documentation_engineer")
    intelligence_model = resolve_model_for_role(model, "github_intelligence")
    grader_model = resolve_model_for_role(model, "documentation_reviewer")

    docs_rubric_grader = build_docs_rubric_grader(
        grader_model, rubric=GROUNDEDNESS_RUBRIC + "\n\n" + COMPLETENESS_RUBRIC
    )

    registry = agents
    classifier_builder = getattr(registry, "classifier", None) or build_classifier
    classifier = classifier_builder(
        classifier_model,
        runtime=steering_runtime,
        agent_id="documentation.classifier",
        node_id="classify",
    )
    context_builder = getattr(registry, "issue_context", None) or build_issue_context_agent
    context_agent = context_builder(
        context_model,
        _dedupe(
            reg.semantic_search,
            reg.keyword_search,
            _LOCAL_CODE_SEARCH,
        ),
        runtime=steering_runtime,
        agent_id="issue.context",
        node_id="context",
    )
    research_builder = getattr(registry, "issue_research_swarm", None) or build_issue_research_swarm
    research_swarm = research_builder(
        research_model,
        reg,
        local_tools=_dedupe(
            reg.semantic_search,
            reg.keyword_search,
            reg.hybrid_search,
            _LOCAL_CODE_SEARCH,
        ),
        runtime=steering_runtime,
        agent_id="issue.research",
        node_id="research",
    )
    analyzer_builder = getattr(registry, "issue_analyzer", None) or build_issue_analyzer
    issue_analyzer = analyzer_builder(
        intelligence_model,
        _dedupe(
            reg.semantic_search,
            reg.keyword_search,
            _LOCAL_CODE_SEARCH,
        ),
        runtime=steering_runtime,
        agent_id="issue.impact",
        node_id="impact",
    )
    answer_builder = getattr(registry, "answer_writer", None) or build_answer_writer
    answer_agent = answer_builder(
        support_model,
        _dedupe(reg.semantic_search, reg.keyword_search),
        runtime=steering_runtime,
        agent_id="issue.answer",
        node_id="answer",
    )
    writer_builder = getattr(registry, "writer_agent", None) or build_writer_agent
    update_writer = writer_builder(
        writer_model,
        scope_writer_tools(reg.documentation_engineer, reg.documentation),
        runtime=steering_runtime,
        agent_id="documentation.writer",
        node_id="update",
    )
    create_writer = writer_builder(
        writer_model,
        scope_writer_tools(reg.documentation_engineer, reg.documentation),
        runtime=steering_runtime,
        agent_id="documentation.writer",
        node_id="create",
    )
    responder_builder = getattr(registry, "issue_responder", None) or build_issue_responder
    responder = responder_builder(
        intelligence_model,
        _dedupe(reg.github_intelligence),
        runtime=steering_runtime,
        agent_id="delivery.github",
        node_id="deliver",
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

    builder.add_node(issue_analyzer, "impact")
    builder.add_edge("research", "impact")

    builder.add_node(answer_agent, "answer")
    builder.add_node(update_writer, "update")
    builder.add_node(create_writer, "create")
    builder.add_edge("impact", "answer", condition=route_to_answer)
    builder.add_edge("impact", "update", condition=route_to_update)
    builder.add_edge("impact", "create", condition=route_to_create)

    evaluator = EvaluatorNode(
        "evaluate",
        max_iterations=evaluator_max_iterations,
        rubric_grader=docs_rubric_grader,
    )
    builder.add_node(evaluator, "evaluate")
    builder.add_edge("answer", "evaluate", condition=generated)
    builder.add_edge("update", "evaluate", condition=generated)
    builder.add_edge("create", "evaluate", condition=generated)
    builder.add_edge("context", "evaluate", condition=generated)
    builder.add_edge("research", "evaluate", condition=generated)

    builder.add_edge("evaluate", "answer", condition=needs_revision_of("answer"))
    builder.add_edge("evaluate", "update", condition=needs_revision_of("update"))
    builder.add_edge("evaluate", "create", condition=needs_revision_of("create"))

    builder.add_node(responder, "deliver")
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
