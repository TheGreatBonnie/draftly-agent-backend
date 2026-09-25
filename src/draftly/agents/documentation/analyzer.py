"""Documentation impact analyzer agent (the ``impact`` node)."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import IMPACT_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import ImpactAnalysis
from draftly.orchestration.graphs.tool_scoping import tool_name
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole

# Read-only repository-navigation tools the impact prompt may name when
# instructing the agent where to enumerate the docs tree. Local-first runs
# register the checkout tools; github-grounding runs register the GitHub API
# equivalents. The hint below is derived from the tools actually passed in, so
# the prompt never instructs a phantom tool name.
_REPO_TOOL_NAMES = (
    "github_get_tree",
    "github_read_file",
    "list_directory",
    "read_file",
    "get_files",
    "code_search",
)

_FALLBACK_REPO_HINT = "your read-only repository tools"


def _repo_tool_hint(tools: list[Any]) -> str:
    """Name the repository tools registered for this run, or a safe fallback."""
    registered = {tool_name(tool) for tool in tools}
    present = [name for name in _REPO_TOOL_NAMES if name in registered]
    return ", ".join(present) if present else _FALLBACK_REPO_HINT


def build_impact_agent(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the impact analysis agent."""

    return build_draftly_agent(
        role=AgentRole.WRITER,
        system_prompt=build_prompt(
            IMPACT_PROMPT,
            output_model=ImpactAnalysis,
            documentation_policy="documentation_policy",
        ).replace("{repo_tool_hint}", _repo_tool_hint(tools)),
        model=model,
        tools=tools,
        structured_output_model=ImpactAnalysis,
        plugins=[
            AgentSkills(
                skills=load_skills(
                    "github-pr-analysis",
                    "documentation-research",
                )
            )
        ],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "impact",
        node_id=node_id or "impact",
        name="impact",
        description="Analyzes documentation impact and decides answer/update/create.",
    )
