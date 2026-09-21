"""Dedicated answer-quality gate for the documentation answer branch.

The answer path shares none of the page-workflow machinery: answers carry
inline content (never draft-store artifacts) and use the same bounded revision
loop as before. This node extracts exactly that state from the generic
``EvaluatorNode``: deterministic score/reasons, optional rubric feedback, and a
bounded revision counter — and nothing else (no page tables, no drafts).
"""

from __future__ import annotations

from typing import Any

import structlog
from strands.multiagent.base import (
    MultiAgentBase,
    MultiAgentResult,
    NodeResult,
    Status,
)

from draftly.orchestration.nodes.base import agent_result, parse_node_input
from draftly.orchestration.nodes.evaluate import (
    _draft_text,
    _research_evidence,
    compute_quality,
)

logger = structlog.get_logger(__name__)


class AnswerQualityNode(MultiAgentBase):
    """Deterministic quality gate for a single free-text answer.

    Mirrors ``EvaluatorNode``'s answer-only path: the draft comes from the
    ``answer`` dependency, evidence from ``context``/``research``, and the
    revision counter escalates the answer to the human gate once the budget is
    exhausted. It never touches page artifacts or workflow tasks.
    """

    def __init__(
        self,
        name: str = "answer_evaluate",
        max_iterations: int = 2,
        *,
        rubric_grader: Any,
    ) -> None:
        if rubric_grader is None:
            raise TypeError(
                "AnswerQualityNode requires a rubric_grader; the LLM grader is "
                "mandatory production wiring so failed answers get actionable "
                "feedback rather than an opaque deterministic score."
            )
        self.name = name
        self.iteration = 0
        self.max_iterations = max_iterations
        self.rubric_grader = rubric_grader

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        self.iteration += 1

        deps = parse_node_input(task)
        answer = deps.get("answer")
        draft = _draft_text(answer) if isinstance(answer, dict) else ""

        evidence = []
        for evidence_source in ("context", "research"):
            evidence = _research_evidence(deps.get(evidence_source))
            if evidence:
                break

        score, reasons = compute_quality(evidence, draft)
        passed = score >= 0.7
        escalated = False

        if not reasons:
            reasons.append(f"Score {score:.2f} (threshold: 0.70)")

        if not passed:
            try:
                grade = await self.rubric_grader.grade(draft=draft, evidence=evidence)
                for reason in grade.reasons:
                    if reason and reason not in reasons:
                        reasons.append(f"[rubric] {reason}")
            except Exception:
                logger.warning("answer_rubric_grader_failed", exc_info=True)

        if not passed and self.iteration >= self.max_iterations:
            # Revision budget exhausted: surface the answer to the human review
            # gate instead of burning max_node_executions on the loop.
            passed = True
            escalated = True
            reasons.append(
                f"Quality threshold not met after {self.iteration} evaluations; "
                "escalated to human review"
            )

        logger.info(
            "answer_evaluate_verdict",
            run_id=(invocation_state or {}).get("run_id"),
            passed=passed,
            score=score,
            escalated=escalated,
            evidence_count=len(evidence),
            draft_chars=len(draft),
            reasons=reasons,
        )

        result = {
            "passed": passed,
            "score": score,
            "reasons": reasons,
            "iteration": self.iteration,
            "escalated": escalated,
        }
        return MultiAgentResult(
            status=Status.COMPLETED,
            results={
                self.name: NodeResult(result=agent_result(result))
            },
        )
