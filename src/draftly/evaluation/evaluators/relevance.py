"""Relevance evaluator (plan §8.2) — OutputEvaluator with a rubric.

The relevance LLM judge grades how well the authored documentation addresses
the requested topic/scope. It uses a rubric-based ``OutputEvaluator`` (scoring
``actual_output``) rather than the trace-level ``ResponseRelevanceEvaluator``
from strands_evals: our live documentation runs do not carry a ``Session``
trajectory, so the trace-parsing judge would always raise instead of scoring
the content.
"""

from __future__ import annotations

from typing import Any

from strands_evals.evaluators import OutputEvaluator
from strands_evals.types import EvaluationData, EvaluationOutput

from draftly.evaluation.evaluators.surface_skip import feedback_skip_output

RELEVANCE_RUBRIC = (
    "Assess whether the documentation is relevant to the requested topic and "
    "scope. It should address the stated question or change directly and avoid "
    "irrelevant or off-topic content. Score 0-1 based on relevance."
)


class _SurfaceAwareRelevanceEvaluator(OutputEvaluator):
    """Relevance judge that treats report surfaces as not applicable.

    The rubric demands documentation that addresses the stated question;
    feedback cases return a gap-analysis report, which can never satisfy it
    and would fail on judge variance instead. Delegates unchanged for
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


def build_relevance_evaluator(model: Any = None) -> OutputEvaluator:
    """Answer-relevance LLM judge; ``model=None`` uses the SDK default."""
    return _SurfaceAwareRelevanceEvaluator(name="relevance", rubric=RELEVANCE_RUBRIC, model=model)
