"""Event classifier agent with structured output."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import CLASSIFIER_PROMPT, build_prompt
from draftly.agents.schemas import EventClassification
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_classifier(
    model: Any,
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the event classifier agent (pure reasoning, no tools)."""

    return build_draftly_agent(
        role=AgentRole.CLASSIFIER,
        system_prompt=build_prompt(
            CLASSIFIER_PROMPT,
            output_model=EventClassification,
        ),
        model=model,
        structured_output_model=EventClassification,
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "event_classifier",
        node_id=node_id or "event_classifier",
        name="event_classifier",
        description="Classifies incoming developer events by surface and impact.",
    )
