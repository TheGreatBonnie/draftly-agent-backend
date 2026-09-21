from __future__ import annotations

from datetime import UTC, datetime

from draftly.app.api.routes.reviews import build_review_display
from draftly.persistence.repositories.reviews import ReviewRecord


def page_result(**overrides: object) -> dict:
    item = {
        "page_id": "readme",
        "path": "README.md",
        "status": "passed",
        "version": 2,
        "attempts": 3,
        "score": 0.97,
        "failed_metrics": ["quality_score"],
        "feedback": ["Add usage example"],
        "escalation_reason": None,
    }
    item.update(overrides)
    return item


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
    assert display["changelog"] is None
    assert display["evaluation"] == {
        "overall_score": None,
        "dimensions": [],
        "reasons": [],
        "count": None,
    }
    assert display["evidence"] == []
    assert display["github_url"] is None


def test_display_maps_changelog_entry() -> None:
    """The gate's changelog payload must surface on the read model so the
    review page can render the proposed changelog entry."""
    row = record(
        detail={
            "summary": "Release v1.1.0",
            "document": {
                "kind": "change_plan",
                "files": [{"path": "docs/whats-new.md", "action": "create"}],
            },
            "changelog": {
                "version": "v1.1.0",
                "date": "2026-09-04",
                "entries": [{"category": "Added", "text": "OAuth login"}],
                "raw_markdown": "## [v1.1.0] - 2026-09-04\n### Added\n- OAuth login",
            },
        }
    )

    display = build_review_display(row, {"pr": None})

    assert display["changelog"]["version"] == "v1.1.0"
    assert display["changelog"]["date"] == "2026-09-04"
    assert display["changelog"]["entries"] == [{"category": "Added", "text": "OAuth login"}]
    assert display["changelog"]["raw_markdown"].startswith("## [v1.1.0]")


def test_display_includes_compact_page_results_passed_and_escalated() -> None:
    pages = [
        page_result(),
        page_result(
            page_id="api",
            path="docs/api.md",
            status="awaiting_human_review",
            score=0.71,
            failed_metrics=[],
            feedback=["Document error codes"],
            escalation_reason="Blocking eager review gate: quality below threshold",
        ),
    ]

    display = build_review_display(record(detail=None), {"pr": None}, page_results=pages)

    assert [page["path"] for page in display["page_results"]] == ["README.md", "docs/api.md"]
    page = display["page_results"][0]
    assert set(page) == {
        "page_id",
        "path",
        "status",
        "version",
        "attempts",
        "score",
        "failed_metrics",
        "feedback",
        "escalation_reason",
    }
    assert "content" not in page
    assert "evidence" not in page


def test_display_page_results_default_to_empty_for_historical_runs() -> None:
    display = build_review_display(record(detail=None), {"pr": None})

    assert display["page_results"] == []
