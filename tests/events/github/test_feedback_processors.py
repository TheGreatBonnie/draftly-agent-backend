"""Tests for GitHub feedback event normalization."""

from __future__ import annotations

from draftly.events.github.issue_comment import IssueCommentProcessor
from draftly.events.github.pull_request_review import PullRequestReviewProcessor
from draftly.events.github.pull_request_review_comment import PullRequestReviewCommentProcessor


def _repo() -> dict[str, object]:
    return {"full_name": "acme/draftly"}


async def test_issue_comment_normalizes_feedback_provenance() -> None:
    event = await IssueCommentProcessor().process(
        {
            "action": "created",
            "issue": {"number": 7, "title": "Docs question", "html_url": "https://gh/7"},
            "comment": {"id": 42, "body": "How do I configure this?", "html_url": "https://gh/c42"},
            "repository": _repo(),
            "sender": {"login": "alice"},
        },
        event_id="delivery-1",
    )

    assert event.event_type == "issue_comment.created"
    assert event.feedback["content"] == "How do I configure this?"
    assert event.feedback["source_event_id"] == "42"
    assert event.feedback["source_url"] == "https://gh/c42"
    assert event.feedback["issue_number"] == 7


async def test_pull_request_review_normalizes_review_state() -> None:
    event = await PullRequestReviewProcessor().process(
        {
            "action": "submitted",
            "pull_request": {"number": 8, "html_url": "https://gh/pr/8"},
            "review": {
                "id": 12,
                "body": "Please clarify the migration docs.",
                "state": "changes_requested",
                "html_url": "https://gh/r12",
            },
            "repository": _repo(),
            "sender": {"login": "bob"},
        }
    )

    assert event.event_type == "pull_request_review.submitted"
    assert event.feedback["review_state"] == "changes_requested"
    assert event.feedback["source_event_id"] == "12"


async def test_pull_request_review_comment_is_not_an_issue_payload() -> None:
    processor = PullRequestReviewCommentProcessor()
    payload = {
        "action": "created",
        "pull_request": {"number": 9, "html_url": "https://gh/pr/9"},
        "comment": {"id": 15, "body": "The example needs an explanation.", "html_url": "https://gh/rc15"},
        "repository": _repo(),
        "sender": {"login": "carol"},
    }

    assert processor.supports(payload) is True
    event = await processor.process(payload)
    assert event.event_type == "pull_request_review_comment.created"
    assert event.feedback["pull_request_number"] == 9
