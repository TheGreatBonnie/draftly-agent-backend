"""GitHub issue context agent: local-repo-first evidence gathering.

Mirrors the documentation graph's ``doc_context`` agent for the issue
surface. Online evaluation backs every case with a local authly checkout and
has no usable GitHub API, so the issue context agent uses a local-first prompt
and avoids wasting turn budget on ``get_issue`` 401s. The repo context (issue
body + relevant doc paths) rides in the task event, so production issue flows
that do carry a real GitHub token remain grounded the same way.
"""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import ISSUE_CONTEXT_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import EvidenceBundle
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_issue_context_agent(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the GitHub issue context agent (local, evidence collection)."""

    return build_draftly_agent(
        role=AgentRole.RESEARCH,
        system_prompt=build_prompt(
            ISSUE_CONTEXT_PROMPT,
            output_model=EvidenceBundle,
            documentation_policy="documentation_policy",
            repository_rules="repository_rules",
        ),
        model=model,
        tools=tools,
        structured_output_model=EvidenceBundle,
        plugins=[
            AgentSkills(
                skills=load_skills(
                    "repository-analysis",
                    "github-issue-analysis",
                )
            )
        ],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "issue_context",
        node_id=node_id or "issue_context",
        name="issue_context",
        description="Collects evidence about the issue from the local repo, search, and docs.",
    )
