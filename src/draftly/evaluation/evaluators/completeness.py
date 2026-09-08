"""Completeness evaluator (plan §8.2) — OutputEvaluator with a rubric.

The rubric is a required positional arg on OutputEvaluator; the model is
injected so offline runs can pass a draftly.models Model or None.
"""

from __future__ import annotations

from typing import Any

from strands_evals.evaluators import OutputEvaluator
from strands_evals.types import EvaluationData, EvaluationOutput

from draftly.evaluation.evaluators.surface_skip import feedback_skip_output

COMPLETENESS_RUBRIC = (
    "Assess whether the documentation answer fully addresses the question. "
    "Pass if it covers the requested scope, includes concrete steps or "
    "references where applicable, and omits nothing essential. Score 0-1 "
    "based on completeness."
)


class _SurfaceAwareCompletenessEvaluator(OutputEvaluator):
    """Completeness judge that treats report surfaces as not applicable.

    The rubric expects the answer to cover a user's documentation request;
    feedback cases return a gap-analysis report (a verdict that no
    documentation is needed), which cannot cover an authoring scope and would
    fail on judge variance instead. Delegates unchanged for
    documentation-authoring surfaces.
    """

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        skip = feedback_skip_output(evaluation_case, self.name)
        if skip is not None:
            return skip
        return super().evaluate(evaluation_case)

    async def evaluate_async(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        skip = feedback_skip_output(evaluation_case, self.name)
        if skip is not None:
            return skip
        return await super().evaluate_async(evaluation_case)


def build_completeness_evaluator(model: Any = None) -> OutputEvaluator:
    return _SurfaceAwareCompletenessEvaluator(name="completeness", rubric=COMPLETENESS_RUBRIC, model=model)
