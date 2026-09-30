from __future__ import annotations

from datetime import UTC, datetime

from draftly.persistence.repositories.reviews import ReviewRecord, review_counts_from_records


class PageScoreClient:
    """Returns page rows for a run, as the repository would query them.

    `scores_by_run` holds raw row values, so a test can include a NULL score and
    check how the caller handles it independently of the SQL filter.
    """

    def __init__(self, scores_by_run: dict[str, list[float | None]]) -> None:
        self.scores_by_run = scores_by_run
        self.queried: list[str] = []

    async def fetch_all(self, query: str, *args) -> list[dict]:
        self.queried.append(query)
        run_id = args[0]
        return [{"score": score} for score in self.scores_by_run.get(run_id, [])]


def review(status: str, detail: dict | None = None, thread_id: str = "run-1") -> ReviewRecord:
    return ReviewRecord(
        id=f"review-{status}-{thread_id}",
        org_id="org-1",
        thread_id=thread_id,
        workflow="documentation",
        tool_name="doc-review",
        tool_args={},
        action_description=None,
        status=status,
        created_at=datetime.now(UTC),
        detail=detail,
    )


async def test_counts_are_truthful_and_urgent_uses_risk_or_score() -> None:
    rows = [
        review("pending", {"classification": {"urgency": "high"}}),
        review("pending", {"evaluation": {"score": 0.79}}),
        review("approved"),
        review("rejected"),
        review("expired", {"decision": "needs_changes"}),
    ]

    assert await review_counts_from_records(rows) == {
        "pending": 2,
        "urgent": 2,
        "approved": 1,
        "needs_changes": 0,
        "rejected": 1,
    }


async def test_urgent_counts_a_low_page_average_for_documentation_reviews() -> None:
    """A documentation review stores no legacy `evaluation.overall_score`, so
    before this the "low score" half of the urgent count never fired."""
    client = PageScoreClient({"run-low": [70.0, 74.0]})
    rows = [
        # 72% average, no risk and no legacy score -> urgent on score alone.
        review("pending", {}, thread_id="run-low"),
        # 95% average -> not urgent.
        review("pending", {}, thread_id="run-high"),
    ]
    client.scores_by_run["run-high"] = [95.0, 96.0]

    counts = await review_counts_from_records(rows, client)

    assert counts["urgent"] == 1


async def test_urgent_averages_only_the_final_score_of_each_page() -> None:
    """The client returns one row per page (the latest artifact), so the mean
    is over final scores only - superseded attempts never reach this query."""
    client = PageScoreClient({"run-1": [100.0, 100.0, 70.0]})

    counts = await review_counts_from_records([review("pending")], client)

    # 90% average -> above the 80 threshold, so not urgent.
    assert counts["urgent"] == 0
    # The query must join latest_artifact_id, not read raw attempt rows.
    assert "latest_artifact_id" in client.queried[0]


async def test_urgent_skips_null_page_scores_rather_than_counting_them_as_zero() -> None:
    """A page with no final score is unknown, not a zero. Counting it as zero
    would push the average down and mark the review urgent for the wrong
    reason. The SQL filters these out, but the guard must hold on its own."""
    client = PageScoreClient({"run-1": [100.0, None, None]})

    counts = await review_counts_from_records([review("pending")], client)

    # Mean of the single real score is 100, so not urgent.
    assert counts["urgent"] == 0


async def test_urgent_ignores_risk_when_no_score_is_available_anywhere() -> None:
    client = PageScoreClient({})
    rows = [review("pending", {"classification": {"risk": "low"}})]

    assert (await review_counts_from_records(rows, client))["urgent"] == 0


async def test_counts_still_work_without_a_database() -> None:
    """Risk-only counting must not depend on page scores being readable."""
    rows = [review("pending", {"classification": {"urgency": "high"}})]

    assert (await review_counts_from_records(rows))["urgent"] == 1


async def test_needs_changes_counts_only_explicit_status_or_decision() -> None:
    explicit_decision = review("pending")
    explicit_decision.decision = "needs_changes"
    rows = [review("needs_changes"), explicit_decision]

    assert (await review_counts_from_records(rows))["needs_changes"] == 2
