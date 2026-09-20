"""Documentation quality evaluator (plan §8.2, §8.10).

Custom strands evaluator reusing ``compute_page_metrics()`` from the runtime
gate so CI and page evaluation measure the same thing.
"""

from __future__ import annotations

from typing import Any

from strands_evals.evaluators.evaluator import Evaluator
from strands_evals.types import EvaluationData, EvaluationOutput

from draftly.orchestration.nodes.evaluate import compute_page_metrics


class DocumentationQualityEvaluator(Evaluator):
    """Deterministic doc-quality scoring shared with the runtime gate.

    Evidence is read from ``case.metadata["evidence"]`` (list of dicts
    with id/topic keys, matching EvaluatorNode input). When absent the
    evaluator scores completeness/length heuristics only.
    """

    def __init__(self, *, name: str | None = None) -> None:
        super().__init__(name=name or "documentation_quality")

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        metadata = getattr(evaluation_case, "metadata", None) or {}
        evidence = metadata.get("evidence") or []
        draft = getattr(evaluation_case, "actual_output", None) or ""

        # A case that expects its run to be BLOCKED (e.g. the unsupported
        # content variant) produces authoring feedback, not documentation.
        # scoring feedback with the doc-quality rubric is structurally
        # impossible (with empty evidence the max achievable score is 0.3 <
        # 0.6 threshold regardless of the output), so it is not applicable and
        # must pass with an explicit reason instead of failing forever.
        if metadata.get("expected_blocked"):
            return [
                EvaluationOutput(
                    score=1.0,
                    test_pass=True,
                    reason="n/a: case expects a blocked run; doc-quality rubric not applicable",
                    label=self.name,
                )
            ]

        # Feedback cases produce a gap report (topic clusters + counts), not
        # authored documentation against evidence, so the doc-quality rubric
        # is structurally unpassable (max 0.3 with no evidence) — pass n/a.
        if metadata.get("surface") == "feedback":
            return [
                EvaluationOutput(
                    score=1.0,
                    test_pass=True,
                    reason=(
                        "n/a: feedback surface produces a gap report; "
                        "doc-quality rubric not applicable"
                    ),
                    label=self.name,
                )
            ]

        metrics = compute_page_metrics(evidence, draft)
        quality = metrics[-1]
        return [
            EvaluationOutput(
                score=round(quality.score, 4),
                test_pass=quality.passed,
                reason="; ".join(metric.reason for metric in metrics),
                label=self.name,
            )
        ]


def build_documentation_quality_evaluator(
    model: Any = None,
    *,
    name: str | None = None,
) -> DocumentationQualityEvaluator:
    """Build a ``DocumentationQualityEvaluator``.

    Accepts an optional ``model`` (ignored) so it conforms to the common
    ``build_*_evaluator(model=...)`` signature shared by the other
    evaluators. Doc quality is deterministic and does not require a model.
    """
    del model  # deterministic — no LLM judge needed
    return DocumentationQualityEvaluator(name=name)
