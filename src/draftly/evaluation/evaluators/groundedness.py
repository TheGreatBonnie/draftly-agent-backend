"""Groundedness evaluator wrapper (plan §8.2)."""

from __future__ import annotations

from typing import Any

from strands_evals.evaluators import FaithfulnessEvaluator


def build_groundedness_evaluator(model: Any = None) -> FaithfulnessEvaluator:
    """Faithfulness LLM judge; ``model=None`` uses SDK default."""
    return FaithfulnessEvaluator(model=model)
