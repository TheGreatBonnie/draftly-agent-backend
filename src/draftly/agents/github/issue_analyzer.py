"""GitHub issue analyzer agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import ISSUE_ANALYZER_PROMPT
from draftly.agents.schemas import ImpactAnalysis


def build_issue_analyzer(
    model: Any,
    tools: list[Any] | None = None,
) -> Agent:
    """Build the GitHub issue analyzer agent."""

    return Agent(
        name="issue_analyzer",
        system_prompt=ISSUE_ANALYZER_PROMPT,
        model=model,
        tools=tools or [],
        structured_output_model=ImpactAnalysis,
        description="Analyzes GitHub issues for documentation gaps.",
    )
