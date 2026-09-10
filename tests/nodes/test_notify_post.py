"""NotifyPostNode posts the notify agent's draft as a PR comment (draft-then-post)."""

from __future__ import annotations

import json

import pytest
from strands.multiagent.base import Status

from draftly.orchestration.nodes.notify_post import NotifyPostNode

EVENT = {
    "event_type": "pull_request.opened",
    "repository": "acme/api",
    "pull_request": {"number": 7, "title": "Fix widget"},
}


class _FakeCommenter:
    """Records create_comment calls; optionally raises."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple] = []
        self.error = error

    async def create_comment(
        self,
        repository: str,
        pull_request_number: int,
        body: str,
    ) -> dict:
        if self.error is not None:
            raise self.error
        self.calls.append((repository, pull_request_number, body))
        return {"id": 1, "html_url": f"https://github/{repository}/pull/{pull_request_number}#issuecomment-1"}


def _blocks(*, should_notify: bool = True, kind: str = "gap_detected", body: str = "draft") -> list[dict]:
    receipt = {"should_notify": should_notify, "kind": kind, "body": body}
    return [
        {"text": f"Original Task: {json.dumps(EVENT)}"},
        {"text": "\nInputs from previous nodes:"},
        {"text": "\nFrom notify:"},
        {"text": f"  - pr_notify: {json.dumps(receipt)}"},
    ]


def _payload(result) -> dict:
    """Extract the notify_post structured payload from its AgentResult message."""
    outer = result.results["notify_post"].result
    return json.loads(outer.message.get("content")[0].get("text"))


class TestNotifyPostNode:
    async def test_posts_when_should_notify(self) -> None:
        commenter = _FakeCommenter()
        node = NotifyPostNode("notify_post", comment_factory=lambda: commenter)
        payload = {"should_notify": True, "kind": "gap_detected", "body": "Draftly will generate docs: docs/widgets.md"}

        result = await node.invoke_async(_blocks(**payload), invocation_state={"run_id": "n-1"})

        assert result.status == Status.COMPLETED
        assert commenter.calls == [("acme/api", 7, payload["body"])]
        assert _payload(result)["posted"] is True

    async def test_skips_when_not_needed(self) -> None:
        commenter = _FakeCommenter()
        node = NotifyPostNode("notify_post", comment_factory=lambda: commenter)

        result = await node.invoke_async(_blocks(should_notify=False), invocation_state={"run_id": "n-2"})

        assert result.status == Status.COMPLETED
        assert commenter.calls == []
        assert _payload(result)["posted"] is False

    async def test_skips_empty_body(self) -> None:
        commenter = _FakeCommenter()
        node = NotifyPostNode("notify_post", comment_factory=lambda: commenter)

        result = await node.invoke_async(_blocks(body=""), invocation_state={"run_id": "n-3"})

        assert commenter.calls == []
        assert _payload(result)["posted"] is False

    async def test_missing_repository_skips(self) -> None:
        commenter = _FakeCommenter()
        node = NotifyPostNode("notify_post", comment_factory=lambda: commenter)
        event = {"event_type": "pull_request.opened", "pull_request": {"number": 7}}
        blocks = [
            {"text": f"Original Task: {json.dumps(event)}"},
            {"text": "\nInputs from previous nodes:"},
            {"text": "\nFrom notify:"},
            {"text": "  - pr_notify: " + json.dumps({"should_notify": True, "kind": "gap_detected", "body": "d"})},
        ]

        result = await node.invoke_async(blocks, invocation_state={"run_id": "n-4"})

        assert commenter.calls == []
        assert result.status == Status.COMPLETED
        assert _payload(result)["posted"] is False

    async def test_missing_pr_number_skips(self) -> None:
        commenter = _FakeCommenter()
        node = NotifyPostNode("notify_post", comment_factory=lambda: commenter)
        event = {"event_type": "pull_request.opened", "repository": "acme/api"}
        blocks = [
            {"text": f"Original Task: {json.dumps(event)}"},
            {"text": "\nInputs from previous nodes:"},
            {"text": "\nFrom notify:"},
            {"text": "  - pr_notify: " + json.dumps({"should_notify": True, "kind": "gap_detected", "body": "d"})},
        ]

        result = await node.invoke_async(blocks, invocation_state={"run_id": "n-5"})

        assert commenter.calls == []
        assert result.status == Status.COMPLETED

    async def test_no_receipt_payload_degrades(self) -> None:
        commenter = _FakeCommenter()
        node = NotifyPostNode("notify_post", comment_factory=lambda: commenter)
        blocks = [
            {"text": f"Original Task: {json.dumps(EVENT)}"},
            {"text": "\nInputs from previous nodes:"},
            {"text": "\nFrom impact:"},
            {"text": '  - Agent: {"action": "none"}'},
        ]

        result = await node.invoke_async(blocks, invocation_state={"run_id": "n-6"})

        assert commenter.calls == []
        assert result.status == Status.COMPLETED
        assert _payload(result)["posted"] is False

    async def test_commenter_error_still_completed(self) -> None:
        commenter = _FakeCommenter(error=RuntimeError("api down"))
        node = NotifyPostNode("notify_post", comment_factory=lambda: commenter)

        result = await node.invoke_async(_blocks(), invocation_state={"run_id": "n-7"})

        assert result.status == Status.COMPLETED
        assert _payload(result)["posted"] is False

    async def test_default_commenter_created_lazily(self) -> None:
        """When no factory is injected, the node must build a commenter only
        at invocation time (so the runner's installation context applies)."""
        created: list = []

        def factory():
            instance = _FakeCommenter()
            created.append(instance)
            return instance

        node = NotifyPostNode("notify_post", comment_factory=factory)
        assert created == []
        await node.invoke_async(_blocks(), invocation_state={"run_id": "n-8"})
        assert len(created) == 1