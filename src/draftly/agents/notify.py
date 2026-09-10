"""PR notify agent (``notify`` node)."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import NOTIFY_PROMPT, build_prompt
from draftly.agents.schemas import NotifyReceipt


def build_notify_agent(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the PR notify draft-composer agent.

    Strictly read-only: it returns a ``NotifyReceipt`` describing what Draftly
    will do for the PR; a deterministic ``notify_post`` node applies the
    posting side effect. The agent must never receive mutation tools.
    """

    return Agent(
        name="pr_notify",
        system_prompt=build_prompt(
            NOTIFY_PROMPT,
            output_model=NotifyReceipt,
        ),
        model=model,
        tools=tools,
        structured_output_model=NotifyReceipt,
        description="Composes a PR comment explaining Draftly's documentation work.",
    )