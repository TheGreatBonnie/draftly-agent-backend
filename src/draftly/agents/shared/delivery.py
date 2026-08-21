"""Delivery agent: opens PRs, posts replies and messages, gated by HITL."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import DELIVERY_PROMPT
from draftly.agents.schemas import DeliveryReceipt


def build_delivery_agent(
    model: Any,
    tools: list[Any],
    *,
    hitl: bool = True,
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
        system_prompt=DELIVERY_PROMPT,
        model=model,
        tools=tools,
        structured_output_model=DeliveryReceipt,
        interventions=interventions,
        description="Delivers the final output (PR, reply, or message).",
    )
