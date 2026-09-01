from __future__ import annotations

from draftly.events.github.pull_request import PullRequestProcessor


def closed_pr_payload(*, merged: bool) -> dict:
    return {
        "action": "closed",
        "delivery_id": "d-merged",
        "repository": {"full_name": "acme/api"},
        "sender": {"login": "dev"},
        "pull_request": {
            "number": 7,
            "title": "Fix widget",
            "state": "closed",
            "merged": merged,
            "head": {"sha": "abc"},
            "base": {"ref": "main"},
        },
    }


async def test_closed_merged_emits_pull_request_merged() -> None:
    processor = PullRequestProcessor()
    event = await processor.process(closed_pr_payload(merged=True))
    assert event.event_type == "pull_request.merged"
    assert event.pull_request["action"] == "merged"


async def test_closed_not_merged_emits_pull_request_closed() -> None:
    processor = PullRequestProcessor()
    event = await processor.process(closed_pr_payload(merged=False))
    assert event.event_type == "pull_request.closed"
    assert event.pull_request["action"] == "closed"


async def test_opened_emits_pull_request_opened() -> None:
    processor = PullRequestProcessor()
    event = await processor.process(
        {
            "action": "opened",
            "repository": {"full_name": "acme/api"},
            "pull_request": {"number": 8, "head": {"sha": "x"}, "base": {"ref": "main"}},
        }
    )
    assert event.event_type == "pull_request.opened"
    assert event.pull_request["action"] == "opened"
