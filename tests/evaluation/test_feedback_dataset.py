"""Feedback dataset ground truth.

Each case's expected_output must be token-coverage attainable by the exact
gap report the feedback surface renders (the deterministic graph run through
the online task). Otherwise expected_contains is structurally capped by
evaluator-preamble wording — the same trap the content dataset once hit with
"Blog and social variants announce the release".
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from strands_evals import Case

from draftly.evaluation.online import build_online_task
from draftly.evaluation.runner import ExpectedContains

FEEDBACK_DATASET = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "draftly"
    / "evaluation"
    / "datasets"
    / "feedback.json"
)


class _NoopClient:
    async def invoke(self, **kwargs):
        raise AssertionError("feedback surface must not invoke the client")


def _dataset_case(case: dict) -> None:
    return None


def test_feedback_expected_outputs_are_coverage_attainable() -> None:
    """For every feedback case, the report the surface actually renders must
    reach the expected_contains coverage threshold against the case's
    expected_output — proving the gate is passable, not preamble-capped."""
    dataset = json.loads(FEEDBACK_DATASET.read_text())
    for case in dataset[0]["cases"]:
        metadata = dict(case["metadata"])
        metadata["surface"] = "feedback"
        task = build_online_task(_NoopClient())
        result = asyncio.run(
            task(
                Case(
                    name=case["name"],
                    input=case["input"],
                    expected_output=case["expected_output"],
                    metadata=metadata,
                )
            )
        )
        expected_tokens = ExpectedContains._significant_tokens(case["expected_output"])
        if not expected_tokens:
            continue
        actual_tokens = ExpectedContains._significant_tokens(result["output"])
        coverage = len(expected_tokens & actual_tokens) / len(expected_tokens)
        assert coverage >= ExpectedContains.COVERAGE_THRESHOLD, (
            f"{case['name']}: coverage {coverage:.2f} < "
            f"{ExpectedContains.COVERAGE_THRESHOLD}; realign expected_output with "
            f"the report the feedback surface renders"
        )