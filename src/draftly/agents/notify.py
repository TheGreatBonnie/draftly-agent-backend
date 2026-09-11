"""PR notify agent (``notify`` node)."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import NOTIFY_PROMPT, build_prompt
from draftly.agents.schemas import NotifyReceipt
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_notify_agent(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the PR notify draft-composer agent.

    Strictly read-only: it returns a ``NotifyReceipt`` describing what Draftly
    will do for the PR; a deterministic ``notify_post`` node applies the
    posting side effect. The agent must never receive mutation tools.
    """

    return build_draftly_agent(
        role=AgentRole.RESEARCH,
        system_prompt=build_prompt(
            NOTIFY_PROMPT,
            output_model=NotifyReceipt,
        ),
        model=model,
        tools=tools,
        structured_output_model=NotifyReceipt,
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "pr_notify",
        node_id=node_id or "pr_notify",
        name="pr_notify",
        description="Composes a PR comment explaining Draftly's documentation work.",
    )
