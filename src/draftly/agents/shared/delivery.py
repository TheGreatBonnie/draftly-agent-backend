"""Delivery agent: opens PRs, posts replies and messages, gated by HITL."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.prompts import DELIVERY_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import DeliveryReceipt


def build_delivery_agent(
    model: Any,
    tools: list[Any],
    *,
    hitl: bool = True,
    skill_names: tuple[str, ...] = ("github-delivery",),
) -> Agent:
    """Build the delivery agent with HumanInTheLoop defense-in-depth."""

    interventions: list[Any] = []

    if hitl:
        from strands.vended_interventions.hitl import HumanInTheLoop

        interventions.append(
            HumanInTheLoop(
                allowed_tools=[],
                classifier=False,
                enable_trust=False,
            )
        )

    return Agent(
        name="delivery",
        system_prompt=build_prompt(
            DELIVERY_PROMPT,
            output_model=DeliveryReceipt,
            repository_rules="repository_rules",
            human_review_policy="human_review_policy",
        ),
        model=model,
        tools=tools,
        structured_output_model=DeliveryReceipt,
        plugins=[AgentSkills(skills=load_skills(*skill_names))],
        interventions=interventions,
        description="Delivers the final output (PR, reply, or message).",
    )
