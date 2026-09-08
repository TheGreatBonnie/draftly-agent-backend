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


def _loaded_skill_names(agent: object) -> set[str]:
    from strands.vended_plugins.skills import AgentSkills

    registry = getattr(agent, "_plugin_registry", None)
    for plugin in getattr(registry, "_plugins", {}).values():
        if isinstance(plugin, AgentSkills):
            return {s.name for s in plugin.get_available_skills()}
    return set()
