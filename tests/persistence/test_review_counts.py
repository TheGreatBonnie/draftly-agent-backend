from __future__ import annotations

from datetime import UTC, datetime

from draftly.persistence.repositories.reviews import ReviewRecord, review_counts_from_records


def review(status: str, detail: dict | None = None) -> ReviewRecord:
    return ReviewRecord(
        id=f"review-{status}",
        org_id="org-1",
        thread_id="run-1",
        workflow="documentation",
        tool_name="doc-review",
        tool_args={},
        action_description=None,
        status=status,
        created_at=datetime.now(UTC),
        detail=detail,
    )


def test_counts_are_truthful_and_urgent_uses_risk_or_score() -> None:
    rows = [
        review("pending", {"classification": {"urgency": "high"}}),
        review("pending", {"evaluation": {"score": 0.79}}),
        review("approved"),
        review("rejected"),
        review("expired", {"decision": "needs_changes"}),
    ]

    assert review_counts_from_records(rows) == {
        "pending": 2,
        "urgent": 2,
        "approved": 1,
        "needs_changes": 0,
        "rejected": 1,
    }


def test_needs_changes_counts_only_explicit_status_or_decision() -> None:
    explicit_decision = review("pending")
    explicit_decision.decision = "needs_changes"
    rows = [review("needs_changes"), explicit_decision]

    assert review_counts_from_records(rows)["needs_changes"] == 2
