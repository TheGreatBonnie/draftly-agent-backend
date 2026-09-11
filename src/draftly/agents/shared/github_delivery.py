"""GitHub delivery agent: opens documentation PRs."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import DELIVERY_PROMPT, build_prompt
from draftly.agents.schemas import DeliveryReceipt
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_github_delivery_agent(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the GitHub delivery agent (branch/commit/PR)."""

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
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "github_delivery",
        node_id=node_id or "github_delivery",
        name="github_delivery",
        description="Opens documentation pull requests on GitHub.",
    )
