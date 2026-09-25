"""build_review_agent produces a REVIEWER agent with ReviewVerdict output."""

from __future__ import annotations

from typing import Any

from draftly.agents.documentation.reviewer import build_review_agent
from draftly.agents.schemas import ReviewVerdict


def test_review_agent_uses_structured_output_for_review_verdict(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_draftly_agent(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("draftly.agents.factory.build_draftly_agent", fake_draftly_agent)
    agent = build_review_agent(object())
    assert captured["structured_output_model"] is ReviewVerdict
    assert agent is not None
    assert captured["node_id"] == "review"


def test_docs_reviewer_agents_register_no_tool_skills() -> None:
    """The documentation reviewer agents run with zero tools (PR runs
    included), so neither may carry skills that advertise ``read_file`` /
    ``list_directory`` / ``validate_links`` they can never call — teaching
    unregistered tools is the phantom-tool failure of run d7cfb2a0."""
    from draftly.agents.documentation.reviewer import (
        build_review_agent,
        build_reviewer_agent,
    )
    from tests.stub_model import StubModel

    model = StubModel()
    for agent in (build_review_agent(model, []), build_reviewer_agent(model, [])):
        plugin = agent._plugin_registry._plugins.get("agent_skills")
        assert plugin is None, (
            f"{agent.name} advertises skills whose tools are never registered"
        )
