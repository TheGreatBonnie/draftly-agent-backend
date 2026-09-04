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

RELEVANCE_RUBRIC = (
    "Assess whether the documentation is relevant to the requested topic and "
    "scope. It should address the stated question or change directly and avoid "
    "irrelevant or off-topic content. Score 0-1 based on relevance."
)


def build_relevance_evaluator(model: Any = None) -> OutputEvaluator:
    """Answer-relevance LLM judge; ``model=None`` uses the SDK default."""
    return OutputEvaluator(name="relevance", rubric=RELEVANCE_RUBRIC, model=model)
