"""Evidence-preserving content strategy agent contract."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.content.prompts import CONTENT_STRATEGIST_PROMPT
from draftly.agents.content.schemas import ContentBriefOutput
from draftly.agents.prompts import build_prompt, load_skills


def build_content_strategist(model: Any, tools: list[Any]) -> Agent:
    return Agent(
        name="content_strategist",
        system_prompt=build_prompt(
            CONTENT_STRATEGIST_PROMPT,
            output_model=ContentBriefOutput,
        ),
        model=model,
        tools=tools,
        structured_output_model=ContentBriefOutput,
        plugins=[AgentSkills(skills=load_skills("content-production"))],
        description="Builds evidence-grounded content briefs.",
    )


class ContentStrategist:
    def build_brief(
        self, *, title: str, summary: str, evidence: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {"title": title, "message": summary, "evidence": evidence}
