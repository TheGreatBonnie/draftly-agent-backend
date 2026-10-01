"""Org-wide page-quality aggregates read the latest artifact for each page."""

from __future__ import annotations

import pytest

from draftly.persistence.repositories.page_quality import PageQualityRepository


class SummaryClient:
    """Emulates the three queries summary() issues, keyed by a SQL marker."""

    def __init__(self, aggregate: dict, metrics: list[dict], trend: list[dict]) -> None:
        self.aggregate = aggregate
        self.metrics = metrics
        self.trend = trend
        self.calls: list[tuple[str, tuple]] = []

    async def fetch_one(self, query: str, *args):
        self.calls.append((query, args))
        return self.aggregate

    async def fetch_all(self, query: str, *args):
        self.calls.append((query, args))
        if "jsonb_array_elements" in query:
            return self.metrics
        return self.trend


async def test_summary_counts_pages_and_runs_for_the_org() -> None:
    client = SummaryClient(
        aggregate={
            "average_score": 0.62,
            "total_runs": 2,
            "scored_pages": 7,
            "total_pages": 9,
            "passed": 5,
            "needs_revision": 1,
            "awaiting_human": 1,
        },
        metrics=[{"metric": "detail", "average_score": 1.0, "sample_count": 7}],
        trend=[{"date": "2026-09-28", "average_score": 0.62, "run_count": 1}],
    )
    repo = PageQualityRepository(client)

    result = await repo.summary("org-1", 14)

    assert result["scored_pages"] == 7
    assert result["total_pages"] == 9
    assert result["average_score"] == pytest.approx(62.0, abs=0.05)
    assert result["by_metric"][0]["metric"] == "detail"
    assert result["trend"][0]["date"] == "2026-09-28"
    # Every query is org-scoped and windowed.
    for sql, params in client.calls:
        assert "org_id = $1" in sql
        assert params[0] == "org-1"
        assert "latest_artifact_id" in sql


async def test_summary_reports_no_score_when_nothing_is_scored() -> None:
    client = SummaryClient(
        aggregate={
            "average_score": None,
            "total_runs": 1,
            "scored_pages": 0,
            "total_pages": 4,
            "passed": 0,
            "needs_revision": 0,
            "awaiting_human": 4,
        },
        metrics=[],
        trend=[],
    )
    repo = PageQualityRepository(client)

    result = await repo.summary("org-1", 7)

    # Unscored pages are unknown, not zero -- avg stays null so the UI shows an em dash.
    assert result["average_score"] is None
    assert result["scored_pages"] == 0
    assert result["total_pages"] == 4
    assert result["by_metric"] == []
    assert result["trend"] == []


async def test_summary_survives_a_missing_aggregate_row() -> None:
    class EmptyClient:
        async def fetch_one(self, query: str, *args):
            return None

        async def fetch_all(self, query: str, *args):
            return []

    result = await PageQualityRepository(EmptyClient()).summary("org-1", 14)

    assert result["average_score"] is None
    assert result["total_runs"] == 0
    assert result["total_pages"] == 0
