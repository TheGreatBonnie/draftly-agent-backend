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
