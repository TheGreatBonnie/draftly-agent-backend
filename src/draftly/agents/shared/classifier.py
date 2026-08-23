"""Event classifier agent with structured output."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import CLASSIFIER_PROMPT, build_prompt
from draftly.agents.schemas import EventClassification


def build_classifier(
    model: Any,
) -> Agent:
    """Build the event classifier agent (pure reasoning, no tools)."""

    return Agent(
        name="event_classifier",
        system_prompt=build_prompt(
            CLASSIFIER_PROMPT,
            output_model=EventClassification,
        ),
        model=model,
        structured_output_model=EventClassification,
        description="Classifies incoming developer events by surface and impact.",
    )
