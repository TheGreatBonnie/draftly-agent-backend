"""Tests for GitHub feedback persistence workflow."""

from __future__ import annotations

from types import SimpleNamespace

from draftly.workflows.context import WorkflowContext
from draftly.workflows.github.feedback_ingestion import ingest_github_feedback
from draftly.workflows.state import WorkflowStatus


class RecordingFeedbackRepository:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.items = []
        self.error = error

    async def save_signal(self, item):
        if self.error:
            raise self.error
        self.items.append(item)
        return item


def _context(repository: RecordingFeedbackRepository) -> WorkflowContext:
    return WorkflowContext(repositories=SimpleNamespace(feedback=repository))


def _event() -> dict:
    return {
        "event_id": "delivery-1",
        "event_type": "issue_comment.created",
        "project_id": "org-a",
        "repository": "acme/draftly",
        "actor": "alice",
        "feedback": {
            "platform": "github",
            "content": "How do I configure this?",
            "source_event_id": "comment-1",
            "source_url": "https://github.com/acme/draftly/issues/1#issuecomment-1",
            "category": "question",
        },
    }


async def test_ingestion_persists_normalized_feedback_before_delivery() -> None:
    repository = RecordingFeedbackRepository()

    state = await ingest_github_feedback(_context(repository), _event())

    assert state.status == WorkflowStatus.DELIVERED
    assert len(repository.items) == 1
    assert repository.items[0].org_id == "org-a"
    assert repository.items[0].source_event_id == "comment-1"
    assert repository.items[0].source_url.endswith("issuecomment-1")


async def test_ingestion_accepts_dispatcher_run_id_kwarg() -> None:
    repository = RecordingFeedbackRepository()

    state = await ingest_github_feedback(
        _context(repository), _event(), run_id="delivery-1"
    )

    assert state.status == WorkflowStatus.DELIVERED
    assert len(repository.items) == 1


async def test_ingestion_fails_when_persistence_fails() -> None:
    repository = RecordingFeedbackRepository(error=RuntimeError("database unavailable"))

    state = await ingest_github_feedback(_context(repository), _event())

    assert state.status == WorkflowStatus.FAILED
    assert "database unavailable" in state.errors[0]
