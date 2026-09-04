"""Documentation context agent: local-repo-first evidence gathering.

Differs from the shared ``context`` agent in two ways that matter for the
online evaluation harness (which backs each case with a local authly worktree
and has no usable GitHub API): it uses a prompt that instructs local-first
evidence gathering, and it omits the network GitHub tools so it cannot waste
turn budget on 401s.
"""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.prompts import DOC_CONTEXT_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import EvidenceBundle


def build_doc_context_agent(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the documentation context agent (local, evidence collection)."""

    return Agent(
        name="doc_context",
        system_prompt=build_prompt(
            DOC_CONTEXT_PROMPT,
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
                    "documentation-research",
                    "documentation-gap-detection",
                )
            )
        ],
        description="Collects evidence about the event from the local repo, search, and docs.",
    )
