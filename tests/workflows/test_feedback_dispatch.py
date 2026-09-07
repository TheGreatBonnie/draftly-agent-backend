"""Tests for durable feedback-gap persistence."""

from __future__ import annotations

from types import SimpleNamespace

from draftly.workflows.context import WorkflowContext
from draftly.workflows.feedback.documentation_feedback_loop import run_feedback_loop
from draftly.workflows.state import WorkflowStatus


class RecordingGapRepository:
    def __init__(self) -> None:
        self.candidates = []
        self.outcomes = []

    async def upsert_candidate(self, org_id, candidate):
        self.candidates.append((org_id, candidate))
        return f"gap-{len(self.candidates)}"

    async def set_outcome(self, gap_id, outcome):
        self.outcomes.append((gap_id, outcome))


async def test_feedback_loop_persists_graph_candidates_for_org() -> None:
    gaps = RecordingGapRepository()
    context = WorkflowContext(
        repositories=SimpleNamespace(documentation_gaps=gaps),
    )
    questions = [
        {
            "topic": "retries",
            "question": "How do retries work?",
            "source": "github",
            "source_event_id": "comment-1",
            "source_url": "https://github/comment-1",
        },
        {
            "topic": "retries",
            "question": "How do retries work in production?",
            "source": "github",
            "source_event_id": "comment-2",
            "source_url": "https://github/comment-2",
        },
    ]

    state = await run_feedback_loop(context, org_id="org-a", questions=questions)

    assert state.status == WorkflowStatus.DELIVERED
    assert len(gaps.candidates) == 1
    assert gaps.candidates[0][0] == "org-a"
    assert gaps.candidates[0][1].metadata["source_event_ids"] == ["comment-1", "comment-2"]
    assert gaps.outcomes == [("gap-1", "documentation")]
