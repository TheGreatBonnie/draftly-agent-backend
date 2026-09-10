"""Contract invariants across the skills consumed by the PR docs graph."""

from __future__ import annotations

from pathlib import Path

from draftly.agents.prompts import load_skills

SKILLS_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "draftly"
    / "skills"
)


def _markdown(name: str) -> str:
    skill = load_skills(name)
    assert skill, f"skill {name} did not load"
    return skill[0].instructions


def test_pr_analysis_skill_has_local_mode_reference() -> None:
    reference = SKILLS_DIR / "github-pr-analysis" / "references" / "local-mode.md"
    assert reference.exists(), "references/local-mode.md is required (F2)"


def test_github_pr_analysis_mentions_local_mode() -> None:
    flat = " ".join(_markdown("github-pr-analysis").split())

    assert "local mode" in flat or "LOCAL mode" in flat
    assert "task context" in flat  # diff already provided in local mode

def test_impact_agent_loads_pr_analysis_and_research_skills() -> None:
    from draftly.agents.documentation.analyzer import build_impact_agent
    from draftly.app.composition.tools import build_tools
    from tests.stub_model import StubModel

    tools = build_tools()
    agent = build_impact_agent(
        StubModel(), [tools.semantic_search, tools.keyword_search]
    )

    assert _loaded_skill_names(agent) == {
        "github-pr-analysis",
        "documentation-research",
    }


def test_github_delivery_skill_commits_to_source_pr() -> None:
    """When the run is attached to a source PR (pull_request.head.ref), the
    github-delivery skill must push to that branch instead of opening a fresh
    docs PR, and record the linkage on the source PR."""
    flat = " ".join(_markdown("github-delivery").split())

    assert "source" in flat
    assert "head branch" in flat
    assert "do NOT open" in flat
    assert "fresh" in flat or "new pull request" in flat


def test_github_delivery_skill_lists_pr_tools() -> None:
    flat = " ".join(_markdown("github-delivery").split())

    assert "create_commit" in flat
    assert "create_pull_request" in flat or "create_branch" in flat


def _loaded_skill_names(agent: object) -> set[str]:
    from strands.vended_plugins.skills import AgentSkills

    registry = getattr(agent, "_plugin_registry", None)
    for plugin in getattr(registry, "_plugins", {}).values():
        if isinstance(plugin, AgentSkills):
            return {s.name for s in plugin.get_available_skills()}
    return set()


def test_agent_builders_are_offline_constructible() -> None:
    """No application agent factory requires live provider keys or an event at
    construction time (final verification, plan Task 10 no-live-key)."""
    from draftly.agents.documentation.analyzer import build_impact_agent
    from draftly.agents.shared import build_classifier, build_delivery_agent
    from draftly.agents.shared.context import build_context_agent
    from draftly.app.composition.tools import build_tools
    from tests.stub_model import StubModel

    tools = build_tools()
    build_classifier(StubModel())
    build_context_agent(StubModel(), [])
    build_delivery_agent(StubModel(), [])
    build_impact_agent(StubModel(), [tools.semantic_search, tools.keyword_search])
