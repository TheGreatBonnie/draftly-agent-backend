"""Documentation quality evaluator (plan §8.2, §8.10).

Custom strands evaluator reusing ``compute_quality()`` from the in-graph
EvaluatorNode so CI and the runtime gate measure the same thing.
"""

from __future__ import annotations

from typing import Any

from strands_evals.evaluators.evaluator import Evaluator
from strands_evals.types import EvaluationData, EvaluationOutput

from draftly.orchestration.nodes.evaluate import compute_quality


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

        score, reasons = compute_quality(evidence, draft)
        return [
            EvaluationOutput(
                score=round(score, 4),
                test_pass=score >= 0.6,
                reason="; ".join(reasons) or "quality below threshold",
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
