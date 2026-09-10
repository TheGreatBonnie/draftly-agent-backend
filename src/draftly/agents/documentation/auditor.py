"""Documentation auditor agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import REVIEWER_PROMPT, build_prompt
from draftly.agents.schemas import EvaluationResult
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_auditor_agent(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the documentation auditor agent (stale/coverage checks)."""

    return build_draftly_agent(
        role=AgentRole.REVIEWER,
        system_prompt=(
            "You audit the documentation store for staleness, broken links, "
            "and coverage gaps. "
            + build_prompt(
                REVIEWER_PROMPT,
                output_model=EvaluationResult,
                evaluation_rules="evaluation_rules",
                documentation_policy="documentation_policy",
            )
        ),
        model=model,
        tools=tools,
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "doc_auditor",
        node_id=node_id or "doc_auditor",
        name="doc_auditor",
        description="Audits documentation for staleness and gaps.",
    )
