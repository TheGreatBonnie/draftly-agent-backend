"""Correctness evaluator wrapper (plan §8.2)."""

from __future__ import annotations

from typing import Any

from strands_evals.evaluators import CorrectnessEvaluator


def build_correctness_evaluator(model: Any = None) -> CorrectnessEvaluator:
    """Factual correctness LLM judge; ``model=None`` uses SDK default."""
    return CorrectnessEvaluator(model=model)
