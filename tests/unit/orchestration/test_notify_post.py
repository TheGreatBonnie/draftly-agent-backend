"""PR impact comments must stay true if the writer later fails."""

import json

from draftly.orchestration.nodes.notify_post import NotifyPostNode


class _Commenter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    async def create_comment(self, repository: str, number: int, body: str) -> dict:
        self.calls.append((repository, number, body))
        return {"id": 42}


async def test_gap_comment_reports_impact_without_promising_completion() -> None:
    commenter = _Commenter()
    node = NotifyPostNode(comment_factory=lambda: commenter)
    sections = [
        'Original Task: {"repository": "acme/api", "pull_request": {"number": 7}}',
        "Inputs from previous nodes:",
        "From impact:",
        "  - Agent: "
        + json.dumps(
            {
                "action": "create",
                "affected_documents": ["docs/oauth.md"],
            }
        ),
    ]

    await node.invoke_async([{"text": "\n".join(sections)}])

    body = commenter.calls[0][2]
    assert "docs/oauth.md" in body
    assert "detected" in body
    assert "will generate" not in body


async def test_comment_uses_impact_paths_even_when_notify_disagrees() -> None:
    commenter = _Commenter()
    node = NotifyPostNode(comment_factory=lambda: commenter)
    sections = [
        'Original Task: {"repository": "acme/api", "pull_request": {"number": 7}}',
        "Inputs from previous nodes:",
        "From impact:",
        "  - Agent: " + json.dumps({"action": "update", "affected_documents": ["docs/oauth.md"]}),
        "From notify:",
        "  - Agent: " + json.dumps({"kind": "no_gap", "body": "wrong"}),
    ]

    await node.invoke_async([{"text": "\n".join(sections)}])

    assert commenter.calls[0][2].endswith("- docs/oauth.md")
