from __future__ import annotations

from datetime import UTC, datetime

from draftly.app.api.routes.reviews import build_review_display
from draftly.persistence.repositories.reviews import ReviewRecord


def record(
    *,
    detail: dict | None = None,
    action_description: str | None = "docs update",
    status: str = "pending",
) -> ReviewRecord:
    return ReviewRecord(
        id="rev-1",
        org_id="org-1",
        thread_id="run-1",
        workflow="documentation",
        tool_name="doc-review",
        tool_args={"interrupt_id": "interrupt-1"},
        action_description=action_description,
        status=status,
        decision=None,
        created_at=datetime(2026, 9, 9, 10, 0, tzinfo=UTC),
        detail=detail,
    )


def test_display_maps_change_plan_fields() -> None:
    row = record(
        detail={
            "summary": "Updated widgets guide",
            "classification": {"change_type": "update", "urgency": "medium"},
            "evaluation": {
                "score": 0.94,
                "passed": True,
                "reasons": ["Grounded in 3/3 sources"],
            },
            "evidence": [{"id": "docs/widgets.md", "topic": "widgets"}],
            "document": {
                "kind": "change_plan",
                "repository": "acme/api",
                "files": [{
                    "path": "docs/widgets.md",
                    "action": "update",
                    "content": "# Widgets",
                    "original_content": "# Old widgets",
                    "original_content_available": True,
                }],
            },
        }
    )

    display = build_review_display(row, {"pr": {"trigger_label": "PR #42"}})

    assert display["title"] == "Updated widgets guide"
    assert display["reference"] == "PR #42"
    assert display["description"] == "Updated widgets guide"
    assert display["repository"] == "acme/api"
    assert display["files"][0]["original_content"] == "# Old widgets"
    assert display["files"][0]["proposed_content"] == "# Widgets"
    assert display["change_type"] == "update"
    assert display["risk"] == "medium"
    assert display["evaluation"]["overall_score"] == 94.0
    assert display["evidence"] == [{"id": "docs/widgets.md", "topic": "widgets"}]


def test_display_maps_github_url_and_updated_timestamp() -> None:
    row = record(
        detail={"document": {"title": "Release notes", "files": []}},
        status="approved",
    )

    display = build_review_display(
        row,
        {
            "pr": {
                "title": "Release notes",
                "trigger_label": "PR #42",
                "owner": "acme",
                "repo": "api",
                "issue_number": 42,
            }
        },
    )

    assert display["github_url"] == "https://github.com/acme/api/pull/42"
    assert display["updated_at"] == row.created_at.isoformat()


def test_display_is_nullable_safe_for_legacy_records() -> None:
    display = build_review_display(record(detail=None), {"pr": None})

    assert display["title"] == "docs update"
    assert display["reference"] is None
    assert display["description"] == "docs update"
    assert display["repository"] is None
    assert display["files"] == []
    assert display["risk"] is None
    assert display["evaluation"] == {
        "overall_score": None,
        "dimensions": [],
        "reasons": [],
        "count": None,
    }
    assert display["evidence"] == []
    assert display["github_url"] is None
