"""Support question analyzer agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import ISSUE_ANALYZER_PROMPT
from draftly.agents.schemas import ImpactAnalysis


def build_question_analyzer(
    model: Any,
    tools: list[Any] | None = None,
) -> Agent:
    """Build the support question analyzer agent."""

    return Agent(
        name="support_analyzer",
        system_prompt=ISSUE_ANALYZER_PROMPT,
        model=model,
        tools=tools or [],
        structured_output_model=ImpactAnalysis,
        description="Analyzes support questions for documentation gaps.",
    )
