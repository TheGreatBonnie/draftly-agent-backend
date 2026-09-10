from __future__ import annotations

from draftly.agents.catalog import AGENT_CATALOG, get_agent_descriptor, list_agent_descriptors


def test_catalog_has_unique_stable_descriptors() -> None:
    descriptors = list_agent_descriptors()

    assert descriptors
    assert len({item.id for item in descriptors}) == len(descriptors)
    assert all(item.id and item.name and item.description for item in descriptors)
    assert all(item.surface for item in descriptors)
    assert all(item.node_ids for item in descriptors)
    assert all(len(item.tool_keys) == len(set(item.tool_keys)) for item in descriptors)


def test_catalog_contains_active_surface_agent_roles() -> None:
    roles = {item.role for item in AGENT_CATALOG}

    assert {
        "classifier",
        "context_agent",
        "delivery_agent",
        "impact_agent",
        "writer_agent",
        "answer_writer",
        "question_analyzer",
        "solution_researcher",
        "issue_analyzer",
        "issue_responder",
        "research_swarm_factory",
        "changelog_agent",
        "content_strategist",
        "blog_writer",
        "social_adapter",
    } <= roles


def test_catalog_lookup_returns_none_for_unknown_id() -> None:
    assert get_agent_descriptor("does-not-exist") is None
    assert get_agent_descriptor("writer_agent") is not None
