"""Social channel adaptation contract."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.content.prompts import SOCIAL_ADAPTER_PROMPT
from draftly.agents.content.schemas import ContentSocialOutput
from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import build_prompt, load_skills
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_social_adapter(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    return build_draftly_agent(
        role=AgentRole.WRITER,
        system_prompt=build_prompt(SOCIAL_ADAPTER_PROMPT, output_model=ContentSocialOutput),
        model=model,
        tools=tools,
        structured_output_model=ContentSocialOutput,
        plugins=[AgentSkills(skills=load_skills("content-production"))],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "content_social_adapter",
        node_id=node_id or "content_social_adapter",
        name="content_social_adapter",
        description="Adapts grounded drafts for LinkedIn and X.",
    )


class SocialAdapter:
    def adapt(
        self,
        *,
        title: str,
        summary: str,
        channel: str,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        body = f"{title}: {summary}"
        return {"title": title, "body": body, "evidence": evidence, "channel": channel}
