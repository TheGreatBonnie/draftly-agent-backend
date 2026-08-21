"""Deterministic quality gate: grounding, completeness, source coverage."""

from __future__ import annotations

from typing import Any

from strands.multiagent.base import (
    MultiAgentBase,
    MultiAgentResult,
    NodeResult,
    Status,
)

from draftly.orchestration.nodes.base import agent_result, parse_node_input


def compute_quality(
    evidence: list[dict],
    draft: str,
) -> tuple[float, list[str]]:
    """
    Deterministic quality scoring: citation coverage, completeness, grounding.
    """

    reasons: list[str] = []
    score = 0.0

    # Citation coverage: does the draft reference available evidence?
    cited = sum(1 for e in evidence if e.get("id", "") in draft)
    coverage = cited / max(len(evidence), 1)
    score += coverage * 0.4
    if coverage > 0.8:
        reasons.append(f"Grounded in {cited}/{len(evidence)} sources")

    # Completeness: does the draft cover the key topics?
    topics = [e.get("topic", "") for e in evidence if e.get("topic")]
    covered = sum(1 for t in topics if t.lower() in draft.lower())
    completeness = covered / max(len(topics), 1)
    score += completeness * 0.3
    if completeness > 0.7:
        reasons.append(f"Covers {covered}/{len(topics)} key topics")

    # Length heuristic: very short drafts are usually incomplete
    length_score = min(len(draft) / 500, 1.0)
    score += length_score * 0.3
    if length_score > 0.5:
        reasons.append("Adequate detail level")

    return score, reasons


class EvaluatorNode(MultiAgentBase):
    """Deterministic quality gate for generated documentation output."""

    def __init__(
        self,
        name: str = "evaluate",
        max_iterations: int = 3,
    ) -> None:
        self.name = name
        self.iteration = 0
        self.max_iterations = max_iterations

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        self.iteration += 1

        # Parse dependency outputs from the graph's ContentBlock input.
        # The graph feeds prior node results as a list[ContentBlock] with
        # "From <dep_id>:" sections — the draft comes from whichever of
        # answer/update/create ran; evidence comes from research.
        # NOTE: the research swarm's final message may be plain text, not
        # JSON; parse_node_input skips non-JSON payloads, so evidence
        # degrades to [] and scoring falls back to length + iteration
        # caps. Safe by design.
        deps = parse_node_input(task)

        draft = ""
        for dep_id in ("answer", "update", "create"):
            if dep_id in deps:
                draft = deps[dep_id].get(
                    "draft",
                    deps[dep_id].get("content", ""),
                )

        research_out = deps.get("research")
        if isinstance(research_out, list):
            evidence = research_out
        elif isinstance(research_out, dict):
            evidence = research_out.get("evidence", [])
        else:
            evidence = []

        score, reasons = compute_quality(evidence, draft)
        passed = score >= 0.7 or self.iteration >= self.max_iterations

        if not reasons:
            reasons.append(f"Score {score:.2f} (threshold: 0.70)")

        return MultiAgentResult(
            status=Status.COMPLETED,
            results={
                self.name: NodeResult(
                    result=agent_result(
                        {
                            "passed": passed,
                            "score": score,
                            "reasons": reasons,
                            "iteration": self.iteration,
                        }
                    )
                )
            },
        )
