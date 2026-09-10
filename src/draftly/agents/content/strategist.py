"""Evidence-preserving content strategy agent contract."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.content.prompts import CONTENT_STRATEGIST_PROMPT
from draftly.agents.content.schemas import ContentBriefOutput
from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import build_prompt, load_skills
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_content_strategist(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    return build_draftly_agent(
        role=AgentRole.WRITER,
        system_prompt=build_prompt(
            CONTENT_STRATEGIST_PROMPT,
            output_model=ContentBriefOutput,
        ),
        model=model,
        tools=tools,
        structured_output_model=ContentBriefOutput,
        plugins=[AgentSkills(skills=load_skills("content-production"))],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "content_strategist",
        node_id=node_id or "content_strategist",
        name="content_strategist",
        description="Builds evidence-grounded content briefs.",
    )


class ContentStrategist:
    def build_brief(
        self, *, title: str, summary: str, evidence: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {"title": title, "message": summary, "evidence": evidence}
