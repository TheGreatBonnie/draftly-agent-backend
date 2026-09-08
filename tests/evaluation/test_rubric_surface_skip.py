"""Authored-documentation rubrics applied to the feedback surface.

The feedback task renders a gap-analysis report (topic clusters + counts + a
"no gaps detected" verdict). The relevance/completeness (and doc-quality)
rubrics score *authored documentation* against the user's requested topic —
structurally unsatisfiable for a report that deliberately provides no
documentation. Application without a skip produced judge variance on the
no-gaps case (flipped 1.0 <-> 0.0 between identical runs), so these rubrics
must be not-applicable for the feedback surface, exactly like the
expected_blocked skip.
"""

from __future__ import annotations

import asyncio

from strands_evals.types import EvaluationData

from draftly.evaluation.evaluators.completeness import (
    build_completeness_evaluator,
)
from draftly.evaluation.evaluators.relevance import build_relevance_evaluator
from draftly.evaluation.evaluators.surface_skip import feedback_skip_output


def _case(surface: str) -> EvaluationData:
    return EvaluationData(
        input="scattered questions",
        actual_output="No gaps detected; all topics are below the threshold of 3",
        metadata={"surface": surface} if surface else {},
    )


def _assert_not_applicable(results) -> None:
    assert len(results) == 1
    assert results[0].test_pass is True
    assert "n/a" in results[0].reason


def test_feedback_skip_returns_none_for_other_surfaces() -> None:
    assert feedback_skip_output(_case("content"), "relevance") is None
    assert feedback_skip_output(_case(""), "relevance") is None


def test_relevance_is_not_applicable_for_feedback_surface() -> None:
    evaluator = build_relevance_evaluator()
    _assert_not_applicable(evaluator.evaluate(_case("feedback")))
    _assert_not_applicable(asyncio.run(evaluator.evaluate_async(_case("feedback"))))


def test_completeness_is_not_applicable_for_feedback_surface() -> None:
    evaluator = build_completeness_evaluator()
    _assert_not_applicable(evaluator.evaluate(_case("feedback")))
    _assert_not_applicable(asyncio.run(evaluator.evaluate_async(_case("feedback"))))