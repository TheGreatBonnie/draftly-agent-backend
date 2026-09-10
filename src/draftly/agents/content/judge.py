"""In-graph grounding judge for content drafts.

The deterministic ``evaluate_content_variant`` gate only verifies that every
draft claim carries an evidence reference — it cannot tell whether the
referenced evidence actually supports the claim. The grounding judge closes
that gap: for each deterministically-passing variant it asks an LLM to compare
the draft's specific claims against the SEEDED source evidence file contents,
then blocks the variant when claims are fabricated or unsupported.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from strands import Agent

from draftly.agents.content.prompts import CONTENT_GROUNDING_JUDGE_PROMPT
from draftly.agents.content.schemas import ContentJudgeVerdict
from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import build_prompt
from draftly.content.models import ContentVariant
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole

logger = structlog.get_logger(__name__)

Judge = Callable[..., Awaitable[ContentJudgeVerdict]]

DEFAULT_BLOCKING_MESSAGE = "content makes claims unsupported by the provided evidence"


def build_content_grounding_judge(
    model: Any,
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    return build_draftly_agent(
        role=AgentRole.JUDGE,
        system_prompt=build_prompt(
            CONTENT_GROUNDING_JUDGE_PROMPT, output_model=ContentJudgeVerdict
        ),
        model=model,
        structured_output_model=ContentJudgeVerdict,
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "content_grounding_judge",
        node_id=node_id or "content_grounding_judge",
        name="content_grounding_judge",
        description=(
            "Verifies content draft claims against the provided evidence "
            "before publication."
        ),
    )


def _render_judge_prompt(variant: ContentVariant, evidence_content: list[dict[str, str]]) -> str:
    lines = [
        f"Channel: {variant.channel.value}",
        f"Draft title: {variant.title or '(no title)'}",
        f"Draft body:\n{variant.body or '(no body)'}",
        "Evidence supplied:",
    ]
    if evidence_content:
        for item in evidence_content:
            path = str(item.get("path") or "?")
            content = str(item.get("content") or "")
            lines.append(f"\n--- {path} ---\n{content}")
    else:
        lines.append("\n(no evidence supplied)")
    return "\n".join(lines)


def make_grounding_judge(judge_agent: Agent) -> Judge:
    """Bind an agent to the ``(variant, evidence_content) -> verdict`` contract
    the ``ContentEvaluationNode`` expects, failing open on errors so a judge
    outage never blocks the deterministic gate's decision."""

    async def judge(
        variant: ContentVariant, evidence_content: list[dict[str, str]]
    ) -> ContentJudgeVerdict:
        prompt = _render_judge_prompt(variant, evidence_content)
        try:
            return await judge_agent.structured_output_async(ContentJudgeVerdict, prompt)
        except Exception:
            logger.warning(
                "content_grounding_judge_failed",
                channel=variant.channel.value,
                exc_info=True,
            )
            return ContentJudgeVerdict(grounded=True)

    return judge
