"""LLM rubric grader: mandatory adjunct to the deterministic gates.

The in-graph ``EvaluatorNode`` / ``ChangelogEvaluatorNode`` are cheap,
deterministic quality gates. To give the revision passes concrete, specific
feedback — e.g. an LLM whose ``reason`` names the missing topics so the
writer can act — every gate is wired with a rubric grader. It uses the Strands
Eval ``OutputEvaluator`` machinery already established in
``draftly.evaluation.evaluators`` (groundedness / completeness / correctness
judges).

The deterministic gates remain the pass/fail signal; the rubric grader only
enriches ``reasons``. It is always wired so the hot revise loop gets LLM
feedback on every failed draft, and cannot be skipped by a ``None`` config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from strands_evals.evaluators import OutputEvaluator
from strands_evals.types import EvaluationData

from draftly.orchestration.nodes.evaluate import EvaluatorNode

CHANGELOG_RUBRIC = (
    "Assess whether the changelog entry follows Keep a Changelog v2.0.0: a valid "
    "version header, ISO 8601 date, one of the six categories (Added, Changed, "
    "Deprecated, Removed, Fixed, Security), breaking changes marked, and clear "
    "user-facing bullet points without jargon. Score 0-1 based on changelog quality."
)


@dataclass
class RubricGrade:
    score: float = 0.0
    passed: bool = False
    reasons: list[str] = field(default_factory=list)


class RubricGrader(Protocol):
    async def grade(self, *, draft: str, evidence: list[dict]) -> RubricGrade:
        ...


class DeterministicRubricGrader:
    """No-op grader that returns an empty grade instantly.

    Used when no LLM model is available (e.g. provider disabled by a
    402 payment failure).  The deterministic gate remains the pass/fail
    signal; this grader only exists so the evaluate nodes can always be
    wired to a real ``RubricGrader`` instance.
    """

    async def grade(self, *, draft: str, evidence: list[dict]) -> RubricGrade:
        return RubricGrade()


class _EvidenceAwareEvaluator(OutputEvaluator):
    """An ``OutputEvaluator`` that renders page evidence as an ``<Evidence>`` block.

    ``OutputEvaluator`` composes its judge prompt from a fixed set of sections
    and silently ignores ``EvaluationData.metadata``, so evidence handed over
    that way never reaches the judge. The groundedness rubric asks whether claims
    are traceable to "the evidence ... or the provided evidence/content files";
    without this block the judge is asked to verify against context it was never
    given. ``build_groundedness_evaluator`` hit the same class of bug and works
    around it with ``uses_environment_state``; a single page is better served by
    a dedicated section.
    """

    def _build_prompt(self, evaluation_case: EvaluationData) -> str | list:
        # strands_evals ships no py.typed, so the base return is Any; declare it
        # or mypy rejects the passthrough below.
        prompt: str | list = super()._build_prompt(evaluation_case)
        evidence = (evaluation_case.metadata or {}).get("evidence") or []
        lines = [
            f"- {item.get('id') or item.get('url') or ''}: {item.get('topic') or ''}".rstrip(
                ": "
            )
            for item in evidence
            if isinstance(item, dict)
        ]
        rendered = "\n".join(line for line in lines if line.strip())
        if not rendered:
            return prompt
        return str(prompt).replace("<Rubric>", f"<Evidence>\n{rendered}\n</Evidence>\n<Rubric>", 1)


class StrandsRubricGrader:
    """Thin adapter over a rubric ``OutputEvaluator``.

    ``evaluator`` is any ``OutputEvaluator`` (e.g. the existing
    groundedness/completeness judges). We score ``actual_output`` (the draft)
    with the judge's rubric, handing the page evidence over on
    ``EvaluationData.metadata``; rendering it into the prompt is the evaluator's
    job, not ours.
    """

    def __init__(self, evaluator: OutputEvaluator, *, rubric: str) -> None:
        self._evaluator = evaluator
        self._rubric = rubric

    async def grade(self, *, draft: str, evidence: list[dict]) -> RubricGrade:
        outputs = await self._evaluator.evaluate_async(
            EvaluationData(
                input="",
                actual_output=draft,
                metadata={"evidence": evidence},
            )
        )
        if not outputs:
            return RubricGrade()
        out = outputs[0]
        return RubricGrade(
            score=float(getattr(out, "score", 0.0) or 0.0),
            passed=bool(getattr(out, "test_pass", False)),
            reasons=[out.reason] if getattr(out, "reason", "") else [],
        )


def build_rubric_grader(evaluator: OutputEvaluator, *, rubric: str) -> RubricGrader:
    """Build a rubric grader wrapper over a concrete ``OutputEvaluator``."""
    return StrandsRubricGrader(evaluator, rubric=rubric)


def build_docs_rubric_grader(model: Any, rubric: str) -> RubricGrader:
    """Build the docs-quality grader (groundedness + completeness judge).

    Degrades to a deterministic no-op grader when ``model`` is ``None``
    (e.g. the review provider is offline) so the evaluate node never
    crashes on a missing model.
    """
    if model is None:
        return DeterministicRubricGrader()
    return StrandsRubricGrader(
        _EvidenceAwareEvaluator(rubric=rubric, model=model, include_inputs=False),
        rubric=rubric,
    )


def build_changelog_rubric_grader(model: Any) -> RubricGrader:
    """Build the changelog-quality grader (Keep a Changelog judge).

    Degrades to a deterministic no-op grader when ``model`` is ``None``.
    """
    if model is None:
        return DeterministicRubricGrader()
    return StrandsRubricGrader(
        _EvidenceAwareEvaluator(
            rubric=CHANGELOG_RUBRIC, model=model, include_inputs=False
        ),
        rubric=CHANGELOG_RUBRIC,
    )


def evaluator_with_grader(grader: RubricGrader) -> EvaluatorNode:
    """Construct an EvaluatorNode wired to its mandatory rubric grader."""
    return EvaluatorNode(rubric_grader=grader)
