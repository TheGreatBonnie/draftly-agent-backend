from __future__ import annotations

import pytest
from pydantic import ValidationError

from draftly.agents.schemas import ReviewCorrection, ReviewVerdict


def test_review_verdict_defaults_to_clean() -> None:
    verdict = ReviewVerdict()
    assert verdict.verdict == "clean"
    assert verdict.corrections == []


def test_review_verdict_rejects_bad_verdict() -> None:
    with pytest.raises(ValidationError):
        ReviewVerdict(verdict="maybe")


def test_review_correction_holds_instructions() -> None:
    correction = ReviewCorrection(
        task_id="docs/a.md", path="docs/a.md", instructions=["tighten prose"]
    )
    assert correction.instructions == ["tighten prose"]
