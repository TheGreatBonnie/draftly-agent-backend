"""Draftly agent composition (plan §7.3).

``AgentRegistry`` holds Strands agent FACTORIES, not instances: agents
are per-run objects (model + scoped tools), and graphs build fresh ones
via ``build_graph_for_run`` (§6). The registry is the single place that
maps roles to constructors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AgentRegistry:
    """Role → agent factory mapping (factories take (model, tools))."""

    classifier: Any = None
    context_agent: Any = None
    delivery_agent: Any = None
    impact_agent: Any = None
    writer_agent: Any = None
    answer_writer: Any = None
    question_analyzer: Any = None
    solution_researcher: Any = None
    issue_analyzer: Any = None
    issue_responder: Any = None
    research_swarm_factory: Any = None
    documentation_context: Any = None
    documentation_research_swarm: Any = None
    issue_context: Any = None
    issue_research_swarm: Any = None
    support_research_swarm: Any = None
    changelog_agent: Any = None
    content_strategist: Any = None
    blog_writer: Any = None
    social_adapter: Any = None


def build_agents(
    *,
    models: Any,
    tools: Any,
    session_manager: Any = None,
) -> AgentRegistry:
    """Build the Draftly agent factory registry.

    Factories are imported lazily so composition never constructs agents
    (and never needs provider keys) at import time.
    """

    del models, tools, session_manager

    from draftly.agents.content.blog_writer import build_blog_writer
    from draftly.agents.content.social_adapter import build_social_adapter
    from draftly.agents.content.strategist import build_content_strategist
    from draftly.agents.documentation.analyzer import build_impact_agent
    from draftly.agents.documentation.changelog import build_changelog_agent
    from draftly.agents.documentation.context import build_doc_context_agent
    from draftly.agents.documentation.research_swarm import build_doc_research_swarm
    from draftly.agents.documentation.writer import build_writer_agent
    from draftly.agents.github.context import build_issue_context_agent
    from draftly.agents.github.issue_analyzer import build_issue_analyzer
    from draftly.agents.github.issue_responder import build_issue_responder
    from draftly.agents.github.research_swarm import build_issue_research_swarm
    from draftly.agents.shared.classifier import build_classifier
    from draftly.agents.shared.context import build_context_agent
    from draftly.agents.shared.delivery import build_delivery_agent
    from draftly.agents.subagents import build_research_swarm
    from draftly.agents.support.answer_writer import build_answer_writer
    from draftly.agents.support.question_analyzer import build_question_analyzer
    from draftly.agents.support.research_swarm import build_support_research_swarm
    from draftly.agents.support.solution_researcher import (
        build_solution_researcher,
    )

    logger.info("agent factory registry built")

    return AgentRegistry(
        classifier=build_classifier,
        context_agent=build_context_agent,
        delivery_agent=build_delivery_agent,
        impact_agent=build_impact_agent,
        writer_agent=build_writer_agent,
        answer_writer=build_answer_writer,
        question_analyzer=build_question_analyzer,
        solution_researcher=build_solution_researcher,
        issue_analyzer=build_issue_analyzer,
        issue_responder=build_issue_responder,
        research_swarm_factory=build_research_swarm,
        documentation_context=build_doc_context_agent,
        documentation_research_swarm=build_doc_research_swarm,
        issue_context=build_issue_context_agent,
        issue_research_swarm=build_issue_research_swarm,
        support_research_swarm=build_support_research_swarm,
        changelog_agent=build_changelog_agent,
        content_strategist=build_content_strategist,
        blog_writer=build_blog_writer,
        social_adapter=build_social_adapter,
    )
