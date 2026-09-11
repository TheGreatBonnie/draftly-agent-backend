"""Immutable metadata for the agents exposed by the Draftly dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class AgentDescriptor:
    """Display and telemetry identity; this never contains a live agent."""

    id: str
    role: str
    name: str
    description: str
    surface: str
    node_ids: tuple[str, ...]
    tool_keys: tuple[str, ...]
    kind: Literal["agent", "composite"] = "agent"
    factory_attr: str | None = None


AGENT_CATALOG: tuple[AgentDescriptor, ...] = (
    AgentDescriptor(
        id="classifier",
        role="classifier",
        name="Event Classifier",
        description="Classifies incoming developer events by surface and impact.",
        surface="shared",
        node_ids=("classify",),
        tool_keys=(),
        factory_attr="classifier",
    ),
    AgentDescriptor(
        id="context_agent",
        role="context_agent",
        name="Context Agent",
        description="Collects evidence about the event from GitHub, search, and docs.",
        surface="shared",
        node_ids=("context",),
        tool_keys=("github_intelligence", "semantic_search", "keyword_search", "hybrid_search"),
        factory_attr="context_agent",
    ),
    AgentDescriptor(
        id="research_swarm_factory",
        role="research_swarm_factory",
        name="Research Swarm",
        description="Coordinates channel-scoped researchers handing evidence to the workflow.",
        surface="research",
        node_ids=("research",),
        tool_keys=("research",),
        kind="composite",
        factory_attr="research_swarm_factory",
    ),
    AgentDescriptor(
        id="impact_agent",
        role="impact_agent",
        name="Impact Analyzer",
        description="Analyzes documentation impact and selects the next workflow action.",
        surface="documentation",
        node_ids=("impact",),
        tool_keys=("documentation",),
        factory_attr="impact_agent",
    ),
    AgentDescriptor(
        id="writer_agent",
        role="writer_agent",
        name="Documentation Writer",
        description="Writes documentation change plans for create and update actions.",
        surface="documentation",
        node_ids=("update", "create"),
        tool_keys=("documentation_engineer",),
        factory_attr="writer_agent",
    ),
    AgentDescriptor(
        id="delivery_agent",
        role="delivery_agent",
        name="Delivery Agent",
        description="Delivers the final output through the configured destination.",
        surface="shared",
        node_ids=("deliver",),
        tool_keys=("github_delivery", "support_engineer"),
        factory_attr="delivery_agent",
    ),
    AgentDescriptor(
        id="answer_writer",
        role="answer_writer",
        name="Support Writer",
        description="Writes grounded answers to support questions.",
        surface="support",
        node_ids=("answer",),
        tool_keys=("support_engineer",),
        factory_attr="answer_writer",
    ),
    AgentDescriptor(
        id="question_analyzer",
        role="question_analyzer",
        name="Support Analyzer",
        description="Analyzes support questions for documentation gaps and intent.",
        surface="support",
        node_ids=("triage",),
        tool_keys=("support_engineer",),
        factory_attr="question_analyzer",
    ),
    AgentDescriptor(
        id="solution_researcher",
        role="solution_researcher",
        name="Solution Researcher",
        description="Researches solutions and supporting evidence for support questions.",
        surface="support",
        node_ids=("impact",),
        tool_keys=("support_engineer",),
        factory_attr="solution_researcher",
    ),
    AgentDescriptor(
        id="issue_analyzer",
        role="issue_analyzer",
        name="Issue Analyzer",
        description="Analyzes GitHub issues for documentation gaps and impact.",
        surface="github",
        node_ids=("impact",),
        tool_keys=("github_intelligence",),
        factory_attr="issue_analyzer",
    ),
    AgentDescriptor(
        id="issue_responder",
        role="issue_responder",
        name="Issue Responder",
        description="Responds to GitHub issues with answers or documentation pointers.",
        surface="github",
        node_ids=("deliver",),
        tool_keys=("support_engineer", "github_delivery"),
        factory_attr="issue_responder",
    ),
    AgentDescriptor(
        id="changelog_agent",
        role="changelog_agent",
        name="Changelog Writer",
        description="Generates release changelog entries from grounded change evidence.",
        surface="documentation",
        node_ids=("changelog",),
        tool_keys=("documentation",),
        factory_attr="changelog_agent",
    ),
    AgentDescriptor(
        id="content_strategist",
        role="content_strategist",
        name="Content Strategist",
        description="Builds evidence-grounded content briefs.",
        surface="content",
        node_ids=("content_brief",),
        tool_keys=("content",),
        factory_attr="content_strategist",
    ),
    AgentDescriptor(
        id="blog_writer",
        role="blog_writer",
        name="Blog Writer",
        description="Writes grounded blog drafts for human review.",
        surface="content",
        node_ids=("content_blog",),
        tool_keys=("content",),
        factory_attr="blog_writer",
    ),
    AgentDescriptor(
        id="social_adapter",
        role="social_adapter",
        name="Social Adapter",
        description="Adapts grounded content drafts for social channels.",
        surface="content",
        node_ids=("content_linkedin", "content_x"),
        tool_keys=("content",),
        factory_attr="social_adapter",
    ),
)


def list_agent_descriptors() -> tuple[AgentDescriptor, ...]:
    return AGENT_CATALOG


def get_agent_descriptor(agent_id: str) -> AgentDescriptor | None:
    """Return the immutable descriptor for a stable dashboard id."""
    return next((item for item in AGENT_CATALOG if item.id == agent_id), None)


def expand_tool_keys(keys: tuple[str, ...]) -> list[str]:
    """Expand catalog tool groups from the single runtime tool registry."""
    from draftly.app.composition.tools import build_tools

    registry = build_tools()
    result: list[str] = []
    seen: set[str] = set()
    for key in keys:
        for tool in getattr(registry, key, []):
            name = str(getattr(tool, "__name__", ""))
            if name and name not in seen:
                seen.add(name)
                result.append(name)
    return result


def agent_id_for_node(surface: str | None, node_id: str | None) -> str | None:
    """Resolve a graph node to an agent without guessing across surfaces."""
    if not node_id:
        return None
    exact = [
        item for item in AGENT_CATALOG
        if node_id in item.node_ids and item.surface == (surface or "")
    ]
    if len(exact) == 1:
        return exact[0].id
    shared = [
        item for item in AGENT_CATALOG
        if node_id in item.node_ids and item.surface == "shared"
    ]
    if len(shared) == 1:
        return shared[0].id
    return None
