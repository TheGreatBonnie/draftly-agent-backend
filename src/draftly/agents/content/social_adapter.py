"""Social channel adaptation contract."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.content.prompts import SOCIAL_ADAPTER_PROMPT
from draftly.agents.content.schemas import ContentSocialOutput
from draftly.agents.prompts import build_prompt, load_skills


def build_social_adapter(model: Any, tools: list[Any]) -> Agent:
    return Agent(
        name="content_social_adapter",
        system_prompt=build_prompt(SOCIAL_ADAPTER_PROMPT, output_model=ContentSocialOutput),
        model=model,
        tools=tools,
        structured_output_model=ContentSocialOutput,
        plugins=[AgentSkills(skills=load_skills("content-production"))],
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
        if channel == "x":
            body = body[:280]
        return {"title": title, "body": body, "evidence": evidence, "channel": channel}
