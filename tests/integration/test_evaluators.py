"""Live LLM-judge evaluators (plan §11.4). Requires DRAFTLY_LIVE=1."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_faithfulness_scores_real_output(model, requires_live):
    """FaithfulnessEvaluator(model=...) judges a real grounded answer."""
    from strands_evals.evaluators.faithfulness_evaluator import (
        FaithfulnessEvaluator,
    )

    evaluator = FaithfulnessEvaluator(model=model)
    case = _case(
        query="How do docs previews work in Draftly?",
        response="Draftly builds a docs preview for every pull request.",
        expected="Docs previews are built per pull request.",
    )

    output = (await evaluator.evaluate_async(case))[0]

    assert 0.0 <= float(output.score) <= 1.0


async def test_output_evaluator_rubric(model, requires_live):
    """OutputEvaluator(rubric=..., model=...) scores against the rubric."""
    from strands_evals.evaluators.output_evaluator import OutputEvaluator

    evaluator = OutputEvaluator(
        rubric="Answer must mention documentation previews",
        model=model,
    )
    case = _case(
        query="What does Draftly generate per PR?",
        response="Draftly generates documentation previews per PR.",
        expected="Documentation previews.",
    )

    output = (await evaluator.evaluate_async(case))[0]

    assert 0.0 <= float(output.score) <= 1.0


def _case(*, query: str, response: str, expected: str):
    from strands_evals.types.evaluation import EvaluationData

    return EvaluationData(input=query, actual_output=response, expected_output=expected)
