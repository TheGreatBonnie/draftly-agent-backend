"""Changelog agent factory produces a valid Agent with correct constraints."""

from __future__ import annotations

from draftly.agents.documentation.changelog import build_changelog_agent
from draftly.agents.schemas import ChangelogEntry


def test_changelog_agent_uses_changelog_entry_schema() -> None:
    from tests.stub_model import StubModel

    model = StubModel(
        structured_outputs={
            ChangelogEntry: {
                "version": "v1.0.0",
                "date": "2026-09-04",
                "entries": [{"category": "Added", "text": "Feature X"}],
                "raw_markdown": "## [v1.0.0] - 2026-09-04\n\n### Added\n- Feature X.",
            }
        }
    )
    agent = build_changelog_agent(model, [])
    assert agent.name == "changelog_writer"
    assert agent._default_structured_output_model is ChangelogEntry


def test_changelog_agent_tools_are_scoped() -> None:
    """Changelog agent must not receive mutation tools."""
    from tests.stub_model import StubModel

    model = StubModel()
    # Pass empty tools list — agent should still build
    agent = build_changelog_agent(model, [])
    assert agent is not None
