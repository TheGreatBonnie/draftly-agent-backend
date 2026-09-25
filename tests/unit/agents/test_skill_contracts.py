"""Contract invariants across the skills consumed by the PR docs graph."""

from __future__ import annotations

import re
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


_LOCAL_ONLY_TOOL_NAMES = {
    "read_file",
    "list_directory",
    "write_file",
    "update_frontmatter",
    "file_exists",
    "code_search",
    "git_diff",
    "git_log",
    "git_status",
}


def _allowed_tools(name: str) -> set[str]:
    text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    matches = re.findall(r"^allowed-tools:\s*(.+)$", text, re.M)
    assert matches, f"{name}/SKILL.md missing allowed-tools frontmatter"
    return set(matches[0].split())


def test_documentation_research_skill_advertises_no_local_repo_tools() -> None:
    """``documentation-research`` is loaded by the impact and context agents,
    which never have ``read_file`` (impact has no read tool in ANY grounding;
    context only in local runs). Its allowed-tools must stay inside the search
    tools every consumer registers, and its steps must not teach ``read_file``
    (the same phantom-tool class as the writer's documentation-update skill in
    run d7cfb2a0)."""
    allowed = _allowed_tools("documentation-research")
    assert not (allowed & _LOCAL_ONLY_TOOL_NAMES), (
        f"documentation-research advertises local-only tools consumers never "
        f"register: {allowed & _LOCAL_ONLY_TOOL_NAMES}"
    )
    assert allowed <= {"semantic_search", "keyword_search", "hybrid_search"}

    text = (SKILLS_DIR / "documentation-research" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "read_file" not in text.replace("github_read_file", ""), (
        "documentation-research steps must not name read_file"
    )


def test_pr_analysis_skill_text_names_no_local_tools_as_github_alternatives() -> None:
    """``github-pr-analysis`` (loaded by the impact agent) must not pair
    local-checkout tool names as alternatives to the GitHub-API tools — in
    GITHUB mode the local names are never registered, so teaching them teaches
    phantom calls."""
    for rel in (
        "github-pr-analysis/SKILL.md",
        "github-pr-analysis/references/documentation-impact.md",
    ):
        text = (SKILLS_DIR / rel).read_text(encoding="utf-8")
        assert "list_directory" not in text, f"{rel} names local-only list_directory"
        assert "read_file" not in text.replace("github_read_file", ""), (
            f"{rel} names local-only read_file"
        )


def _tool_references(text: str) -> set[str]:
    """Tool names referenced as inline code (`` `name` ``) in skill markdown.

    Skills name tools as inline code; fenced examples legitimately contain shell
    text (``bash``, ``curl``), so scanning bare words would flag code samples.
    """
    return set(re.findall(r"`([A-Za-z_][A-Za-z0-9_.]*)`", text))


def test_writer_skills_name_no_grounding_specific_tool() -> None:
    """The writer's skills must not name tools whose registration depends on the
    run's grounding.

    ``documentation-update``/``documentation-generation`` are attached to every
    writer run, but ``read_file``/``list_directory`` exist only in LOCAL
    grounding while ``github_read_file``/``github_get_tree`` exist only in GITHUB
    grounding. Run e1e96f90: the skill's "``read_file`` … does not exist for you"
    line kept the phantom name in front of the model, which called it 10 times
    (and invented ``bash``) instead of drafting. Skills must describe the run's
    registered tools without naming any of them.
    """
    grounding_specific = {
        "read_file",
        "write_file",
        "list_directory",
        "file_exists",
        "code_search",
        "git_diff",
        "git_log",
        "git_status",
        "github_read_file",
        "github_get_tree",
        "github_search_code",
        "bash",
    }
    for name in ("documentation-update", "documentation-generation"):
        text = _markdown(name)
        references = SKILLS_DIR / name / "references"
        if references.is_dir():
            for reference in sorted(references.glob("*.md")):
                text += "\n" + reference.read_text(encoding="utf-8")
        named = _tool_references(text) & grounding_specific
        assert not named, (
            f"{name} names grounding-specific tools {sorted(named)}; describe "
            "the run's registered tools instead"
        )


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
