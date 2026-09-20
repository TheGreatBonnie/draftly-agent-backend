"""Stage 4 regression lock: health formula byte-for-byte; public freshness
flows through last_committed_dates (source_updated_at/indexed_at), neutral
0.5 when dates are missing.

No production change is expected here — these tests lock the formula
against future edits. Plan: plans/2026-09-20-tavily-rag.md (Task 14).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from draftly.workflows.onboarding.stages import EvaluationResult, run_health_report


def _eval(score: float = 0.8) -> EvaluationResult:
    return EvaluationResult(
        score=score,
        dimensions={
            "coverage": score,
            "completeness": score,
            "structure": score,
            "length": score,
        },
    )


def test_public_freshness_uses_source_updated_at() -> None:
    now = datetime.now(UTC)
    dates = [now - timedelta(days=9), now - timedelta(days=9)]

    result = run_health_report(
        eval_result=_eval(),
        document_count=10,
        section_count=20,
        last_committed_dates=dates,
    )

    assert result.dimensions["freshness"] == pytest.approx(1.0 - 9 / 90)


def test_public_freshness_stale_dates_decay() -> None:
    now = datetime.now(UTC)
    dates = [now - timedelta(days=45)]

    result = run_health_report(
        eval_result=_eval(),
        document_count=10,
        section_count=20,
        last_committed_dates=dates,
    )

    assert result.dimensions["freshness"] == pytest.approx(0.5)


@pytest.mark.parametrize("dates", [[], [None, None], None])
def test_public_freshness_neutral_when_dates_missing(dates) -> None:
    result = run_health_report(
        eval_result=_eval(),
        document_count=10,
        section_count=20,
        last_committed_dates=dates,
    )

    assert result.dimensions["freshness"] == 0.5


def test_health_formula_unchanged() -> None:
    result = run_health_report(
        eval_result=_eval(score=0.8),
        document_count=25,
        section_count=50,
        last_committed_dates=[],
    )

    # 0.7*0.8 + 0.15*min(25/50,1) + 0.15*min((50/25)/5,1)
    assert result.score == pytest.approx(0.56 + 0.075 + 0.06)
    assert result.dimensions["coverage"] == pytest.approx(0.8)
    assert result.dimensions["structure"] == pytest.approx(0.8)
    assert result.dimensions["completeness"] == pytest.approx(0.8)
