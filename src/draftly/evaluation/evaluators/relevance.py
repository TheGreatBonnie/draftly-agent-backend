"""Response relevance evaluator wrapper (plan §8.2)."""

from __future__ import annotations

from typing import Any

from strands_evals.evaluators import ResponseRelevanceEvaluator


def build_relevance_evaluator(model: Any = None) -> ResponseRelevanceEvaluator:
    """Answer-relevance LLM judge; ``model=None`` uses SDK default."""
    return ResponseRelevanceEvaluator(model=model)
