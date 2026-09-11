"""Delivery agent: opens PRs, posts replies and messages, gated by HITL."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import DELIVERY_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import DeliveryReceipt
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_delivery_agent(
    model: Any,
    tools: list[Any],
    *,
    hitl: bool = True,
    skill_names: tuple[str, ...] = ("github-delivery",),
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
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

    return build_draftly_agent(
        role=AgentRole.DELIVERY,
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
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "delivery",
        node_id=node_id or "delivery",
        name="delivery",
        description="Delivers the final output (PR, reply, or message).",
    )
