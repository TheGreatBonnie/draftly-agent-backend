"""Impact-derived PR notifications."""

from __future__ import annotations

import json

from strands.multiagent.base import Status

from draftly.orchestration.nodes.notify_post import NotifyPostNode

EVENT = {
    "event_type": "pull_request.opened",
    "repository": "acme/api",
    "pull_request": {"number": 7, "title": "Fix widget"},
}


class _FakeCommenter:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, int, str]] = []
        self.error = error

    async def create_comment(self, repository: str, number: int, body: str) -> dict:
        if self.error:
            raise self.error
        self.calls.append((repository, number, body))
        return {"id": 1}


def _blocks(impact: dict | None, event: dict = EVENT, notify: dict | None = None) -> list[dict]:
    lines = [f"Original Task: {json.dumps(event)}", "Inputs from previous nodes:"]
    if impact is not None:
        lines.extend(["From impact:", "  - Agent: " + json.dumps(impact)])
    if notify is not None:
        lines.extend(["From notify:", "  - Agent: " + json.dumps(notify)])
    return [{"text": "\n".join(lines)}]


def _payload(result) -> dict:
    outer = result.results["notify_post"].result
    return json.loads(outer.message["content"][0]["text"])


class TestNotifyPostNode:
    async def test_posts_detected_paths_from_impact(self) -> None:
        commenter = _FakeCommenter()
        node = NotifyPostNode(comment_factory=lambda: commenter)
        result = await node.invoke_async(
            _blocks({"action": "update", "affected_documents": ["docs/widgets.md"]}),
            invocation_state={"run_id": "n-1"},
        )
        assert result.status == Status.COMPLETED
        assert commenter.calls == [
            ("acme/api", 7, "Draftly detected documentation work for this PR:\n- docs/widgets.md")
        ]
        assert _payload(result)["posted"] is True

    async def test_no_gap_comes_from_impact_even_if_notify_disagrees(self) -> None:
        commenter = _FakeCommenter()
        node = NotifyPostNode(comment_factory=lambda: commenter)
        await node.invoke_async(
            _blocks({"action": "none"}, notify={"kind": "gap_detected", "body": "wrong"})
        )
        assert commenter.calls[0][2] == "Draftly found no documentation changes needed for this PR."

    async def test_missing_impact_skips(self) -> None:
        commenter = _FakeCommenter()
        node = NotifyPostNode(comment_factory=lambda: commenter)
        result = await node.invoke_async(_blocks(None))
        assert commenter.calls == []
        assert _payload(result)["reason"] == "no_impact"

    async def test_missing_target_skips(self) -> None:
        commenter = _FakeCommenter()
        node = NotifyPostNode(comment_factory=lambda: commenter)
        result = await node.invoke_async(
            _blocks({"action": "create"}, {"pull_request": {"number": 7}})
        )
        assert commenter.calls == []
        assert _payload(result)["reason"] == "no_target"

    async def test_commenter_error_still_completes(self) -> None:
        commenter = _FakeCommenter(error=RuntimeError("api down"))
        node = NotifyPostNode(comment_factory=lambda: commenter)
        result = await node.invoke_async(_blocks({"action": "create"}))
        assert result.status == Status.COMPLETED
        assert _payload(result)["posted"] is False

    async def test_default_commenter_created_lazily(self) -> None:
        created: list[_FakeCommenter] = []

        def factory():
            instance = _FakeCommenter()
            created.append(instance)
            return instance

        node = NotifyPostNode(comment_factory=factory)
        assert created == []
        await node.invoke_async(_blocks({"action": "create"}))
        assert len(created) == 1
