"""ReviewNode deterministic summary derivation and behavior tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

from draftly.agents.schemas import ReviewCorrection, ReviewVerdict
from draftly.orchestration.nodes.review import ReviewNode, page_summaries

_CONTENT = (
    "# Widgets Guide\n\nwidgets intro paragraph.\n\n"
    "[usage](/#usage) and [api](/#api)\n\n"
    "## References\n\n1. [docs/widgets.md](docs/widgets.md)\n"
    "2. [docs/api.md](docs/api.md)\n"
)


def _store() -> SimpleNamespace:
    async def get_latest(*, run_id: str):
        return [SimpleNamespace(path="docs/widgets.md", content=_CONTENT)]

    return SimpleNamespace(get_latest=get_latest)


async def test_page_summaries_extract_heading_links_first_para_refs() -> None:
    summaries = await page_summaries(
        _store(), "run-1",
        [{"task_id": "docs/widgets.md", "path": "docs/widgets.md"}],
    )
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["path"] == "docs/widgets.md"
    assert summary["headings"] == ["Widgets Guide"]
    assert summary["links"] == ["/#usage", "/#api"]
    assert "widgets intro paragraph." in summary["first_paragraph"]
    assert summary["references"] == 2
    assert summary["char_length"] == len(_CONTENT)


async def test_page_summaries_empty_without_store() -> None:
    assert await page_summaries(None, "run-1", []) == []


class _FakeReviewer:
    def __init__(self, verdict: ReviewVerdict) -> None:
        self.verdict = verdict
        self.prompt: str | None = None

    async def invoke_async(self, prompt: str, invocation_state=None, **kwargs):
        self.prompt = prompt
        return SimpleNamespace(structured_output=self.verdict)


def _document_payload() -> dict:
    return {
        "repository": "acme/api",
        "branch": "docs/fanout",
        "commit_message": "docs",
        "summary": "fan-out",
        "files": [{"path": "docs/a.md", "action": "update"}],
        "tasks": [
            {
                "task_id": "docs/a.md",
                "path": "docs/a.md",
                "action": "update",
                "ok": True,
                "reasons": [],
                "evidence_refs": [],
            }
        ],
        "task_count": 1,
        "failed_tasks": [],
    }


def _input(document: dict) -> list[dict]:
    text = (
        "Original Task: {}\n"
        "Inputs from previous nodes:\n"
        "From document:\n"
        "  - Agent: " + json.dumps(document)
    )
    return [{"text": text}]


async def test_review_node_clean_verdict_forwards_plan() -> None:
    reviewer = _FakeReviewer(ReviewVerdict(verdict="clean"))
    node = ReviewNode(reviewer_factory=lambda: reviewer)

    result = await node.invoke_async(_input(_document_payload()), {"run_id": "run-1"})

    payload = json.loads(result.results["review"].result.message["content"][0]["text"])
    assert payload["verdict"] == "clean"
    assert payload["corrections"] == []
    assert payload["repository"] == "acme/api"
    assert payload["files"] == [{"path": "docs/a.md", "action": "update"}]
    assert reviewer.prompt is not None
    assert "docs/a.md" in reviewer.prompt


async def test_review_node_filters_corrections_to_known_tasks() -> None:
    reviewer = _FakeReviewer(
        ReviewVerdict(
            verdict="correct",
            corrections=[
                ReviewCorrection(
                    task_id="docs/a.md", path="docs/a.md", instructions=["tighten"]
                ),
                ReviewCorrection(
                    task_id="docs/ghost.md",
                    path="docs/ghost.md",
                    instructions=["nope"],
                ),
            ],
        )
    )
    node = ReviewNode(reviewer_factory=lambda: reviewer)

    result = await node.invoke_async(_input(_document_payload()), {"run_id": "run-1"})

    payload = json.loads(result.results["review"].result.message["content"][0]["text"])
    assert payload["verdict"] == "correct"
    assert [c["task_id"] for c in payload["corrections"]] == ["docs/a.md"]


async def test_review_node_degrades_to_clean_without_verdict() -> None:
    class _EmptyReviewer:
        async def invoke_async(self, prompt: str, invocation_state=None, **kwargs):
            return SimpleNamespace(structured_output=None)

    node = ReviewNode(reviewer_factory=_EmptyReviewer)
    result = await node.invoke_async(_input(_document_payload()), {"run_id": "run-1"})

    payload = json.loads(result.results["review"].result.message["content"][0]["text"])
    assert payload["verdict"] == "clean"
