from __future__ import annotations

import json

from strands_evals.types.evaluation_report import EvaluationReport

from draftly.evaluation.runner import report_detail_rows


def test_report_detail_rows_preserves_detail_fields_without_sensitive_metadata() -> None:
    report = EvaluationReport(
        overall_score=0.8,
        scores=[0.8],
        test_passes=[True],
        cases=[{"name": "oauth-auth", "evaluator": "expected_contains"}],
        reasons=["concepts matched"],
    )
    cases = [
        {
            "name": "oauth-auth",
            "input": "Explain the OAuth flow",
            "expected_output": "OAuth uses a callback",
            "metadata": {
                "Authorization": "Bearer should-not-appear",
                "provider_key": "provider-secret",
                "_evaluation_detail": {
                    "actual_output": "OAuth uses a callback and state",
                    "evidence": [{"path": "docs/oauth.md", "content": "callback"}],
                    "trace_id": "trace-1",
                    "duration_ms": 123,
                },
            },
        }
    ]

    rows = report_detail_rows(
        "documentation",
        report,
        cases=cases,
        run_id="run-1",
    )

    assert rows == [
        {
            "dataset": "documentation",
            "case_id": "oauth-auth",
            "case": "oauth-auth",
            "metric": "expected_contains",
            "threshold": 0.6,
            "score": 0.8,
            "test_pass": True,
            "reason": "concepts matched",
            "input": "Explain the OAuth flow",
            "expected_output": "OAuth uses a callback",
            "actual_output": "OAuth uses a callback and state",
            "evidence": [{"path": "docs/oauth.md", "content": "callback"}],
            "trace_id": "trace-1",
            "duration_ms": 123,
        }
    ]

    serialized = json.dumps(rows)
    assert "Authorization" not in serialized
    assert "provider-secret" not in serialized


def test_report_detail_rows_uses_nulls_when_task_produced_no_detail() -> None:
    report = EvaluationReport(
        overall_score=0,
        scores=[0],
        test_passes=[False],
        cases=[{"name": "missing-output", "evaluator": "expected_contains"}],
        reasons=["task failed"],
    )

    rows = report_detail_rows(
        "support",
        report,
        cases=[{"name": "missing-output", "input": "question"}],
        run_id="run-2",
    )

    assert rows[0]["input"] == "question"
    assert rows[0]["expected_output"] is None
    assert rows[0]["actual_output"] is None
    assert rows[0]["evidence"] == []
    assert rows[0]["trace_id"] is None
