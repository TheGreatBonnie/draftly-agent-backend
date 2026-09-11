from __future__ import annotations

from types import SimpleNamespace

from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import WorkflowRunner
from draftly.workflows.state import WorkflowState


class FakeDocuments:
    def __init__(self, rows: dict[tuple[str, str, str], dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, str, str]] = []

    async def get_by_org_repository_path(self, *, org_id: str, repository: str, path: str):
        self.calls.append((org_id, repository, path))
        return self.rows.get((org_id, repository, path))


async def test_enrich_review_reason_adds_org_scoped_original_content() -> None:
    documents = FakeDocuments(
        {
            ("org-1", "acme/api", "docs/widgets.md"): {
                "content": "# Existing widgets",
            }
        }
    )
    context = WorkflowContext(repositories=SimpleNamespace(documents=documents))
    runner = WorkflowRunner(context, graph_factory=lambda *_: None)
    state = WorkflowState(run_id="run-1", event={"project_id": "org-1"})
    reason = {
        "document": {
            "repository": "acme/api",
            "files": [{"path": "docs/widgets.md", "action": "update", "content": "# New widgets"}],
        }
    }

    enriched = await runner._enrich_review_reason(reason, state)

    file = enriched["document"]["files"][0]
    assert file["original_content"] == "# Existing widgets"
    assert file["original_content_available"] is True
    assert documents.calls == [("org-1", "acme/api", "docs/widgets.md")]
    assert "original_content" not in reason["document"]["files"][0]


async def test_enrich_review_reason_marks_missing_original_content_unavailable() -> None:
    documents = FakeDocuments({})
    context = WorkflowContext(repositories=SimpleNamespace(documents=documents))
    runner = WorkflowRunner(context, graph_factory=lambda *_: None)
    state = WorkflowState(run_id="run-2", event={"project_id": "org-1"})
    reason = {
        "document": {
            "repository": "acme/api",
            "files": [{"path": "docs/new.md", "action": "create", "content": "# New"}],
        }
    }

    enriched = await runner._enrich_review_reason(reason, state)

    file = enriched["document"]["files"][0]
    assert file["original_content"] is None
    assert file["original_content_available"] is False
