"""Completeness evaluator (plan §8.2) — OutputEvaluator with a rubric.

The rubric is a required positional arg on OutputEvaluator; the model is
injected so offline runs can pass a draftly.models Model or None.
"""

from __future__ import annotations

from typing import Any

from strands_evals.evaluators import OutputEvaluator

COMPLETENESS_RUBRIC = (
    "Assess whether the documentation answer fully addresses the question. "
    "Pass if it covers the requested scope, includes concrete steps or "
    "references where applicable, and omits nothing essential. Score 0-1 "
    "based on completeness."
)


def build_completeness_evaluator(model: Any = None) -> OutputEvaluator:
    return OutputEvaluator(name="completeness", rubric=COMPLETENESS_RUBRIC, model=model)
