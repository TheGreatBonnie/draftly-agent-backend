"""Grounded blog writer contract."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.content.prompts import BLOG_WRITER_PROMPT
from draftly.agents.content.schemas import ContentDraftOutput
from draftly.agents.prompts import build_prompt, load_skills


def build_blog_writer(model: Any, tools: list[Any]) -> Agent:
    return Agent(
        name="content_blog_writer",
        system_prompt=build_prompt(BLOG_WRITER_PROMPT, output_model=ContentDraftOutput),
        model=model,
        tools=tools,
        structured_output_model=ContentDraftOutput,
        plugins=[AgentSkills(skills=load_skills("content-production"))],
        description="Writes grounded blog drafts for human review.",
    )


class BlogWriter:
    def write(
        self, *, title: str, summary: str, evidence: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "title": title,
            "body": f"{summary}\n\nWhat changed\n\n{title}.",
            "evidence": evidence,
        }
