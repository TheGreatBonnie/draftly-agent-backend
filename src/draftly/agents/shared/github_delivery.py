"""GitHub delivery agent: opens documentation PRs."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import DELIVERY_PROMPT, build_prompt
from draftly.agents.schemas import DeliveryReceipt


def build_github_delivery_agent(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the GitHub delivery agent (branch/commit/PR)."""

    return Agent(
        name="github_delivery",
        system_prompt=build_prompt(
            DELIVERY_PROMPT,
            output_model=DeliveryReceipt,
            repository_rules="repository_rules",
            human_review_policy="human_review_policy",
        ),
        model=model,
        tools=tools,
        structured_output_model=DeliveryReceipt,
        description="Opens documentation pull requests on GitHub.",
    )
