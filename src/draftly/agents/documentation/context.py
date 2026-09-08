"""Documentation context agent: grounding-aware evidence gathering.

The evidence mode follows the run's grounding (resolved by the workflow
runner): ``local`` uses the repository-checkout tools and the LOCAL note,
``github`` uses the GitHub API tools and the GitHub-first prompt, and
``docs`` (no checkout, no API) omits the LOCAL note entirely so agents never
invent a repo_dir. The default is local-first, which the online evaluation
harness (local authly worktree, no usable GitHub API) depends on.
"""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.prompts import (
    CONTEXT_PROMPT,
    DOC_CONTEXT_PROMPT,
    LOCAL_REPO_NOTE,
    build_prompt,
    load_skills,
    local_repo_note_for,
)
from draftly.agents.schemas import EvidenceBundle
from draftly.workflows.grounding import GITHUB, LOCAL


def build_doc_context_agent(
    model: Any,
    tools: list[Any],
    *,
    grounding: str = "local",
    repo_dir: str | None = None,
) -> Agent:
    """Build the documentation context agent for the run's grounding mode."""

    if grounding == GITHUB:
        system_prompt = build_prompt(
            CONTEXT_PROMPT,
            output_model=EvidenceBundle,
            documentation_policy="documentation_policy",
            repository_rules="repository_rules",
        )
    else:
        if grounding == LOCAL:
            note = local_repo_note_for(repo_dir) if repo_dir else LOCAL_REPO_NOTE
        else:
            note = ""
        system_prompt = build_prompt(
            DOC_CONTEXT_PROMPT,
            output_model=EvidenceBundle,
            documentation_policy="documentation_policy",
            repository_rules="repository_rules",
            local_repo_note=note,
        )

    return Agent(
        name="doc_context",
        system_prompt=system_prompt,
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
