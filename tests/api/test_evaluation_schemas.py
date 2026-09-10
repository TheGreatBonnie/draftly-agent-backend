from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from draftly.app.api.evaluation_schemas import (
    EvaluationCaseResult,
    EvaluationRunRequest,
    EvaluationRunSummary,
)


def test_evaluation_run_summary_accepts_percentage_score_and_iso_timestamp() -> None:
    record = EvaluationRunSummary(
        id="evaluation-1",
        run_id="run-1",
        name="Documentation evaluation",
        evaluation_type="documentation",
        datasets=["documentation"],
        cases=2,
        passed=1,
        failed=1,
        score=50,
        status="failed",
        started_at=datetime(2026, 9, 10, tzinfo=UTC),
        completed_at=datetime(2026, 9, 10, 0, 1, tzinfo=UTC),
        duration_ms=60000,
    )

    assert record.score == 50
    assert record.started_at.isoformat() == "2026-09-10T00:00:00+00:00"


def test_evaluation_run_summary_rejects_out_of_range_score_and_status() -> None:
    with pytest.raises(ValidationError):
        EvaluationRunSummary(
            id="evaluation-1",
            run_id="run-1",
            name="Documentation evaluation",
            evaluation_type="documentation",
            datasets=[],
            cases=0,
            passed=0,
            failed=0,
            score=101,
            status="unknown",
        )


def test_case_result_allows_missing_sensitive_detail() -> None:
    result = EvaluationCaseResult(
        id="case-result-1",
        run_id="run-1",
        evaluation_id="evaluation-1",
        dataset="documentation",
        case_id="case-1",
        metric="groundedness",
        threshold=80,
        score=72,
        passed=False,
        reason="Evidence was incomplete.",
        input=None,
        expected_output=None,
        actual_output=None,
        evidence=[],
        trace_id=None,
        duration_ms=None,
    )

    assert result.actual_output is None
    assert result.evidence == []


def test_run_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        EvaluationRunRequest(live=False, profile=None, unexpected=True)
