import json
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

from draftly.content.models import ContentRequest
from draftly.integrations.database.client import DatabaseClient
from draftly.persistence.repositories.content import ContentRepository
from draftly.workflows.content.content_generation import run_content_generation
from draftly.workflows.state import WorkflowStatus


@dataclass
class FakeDb:
    package_row: dict[str, Any] = field(default_factory=lambda: {
        "package_id": "package-1", "org_id": "org-1", "repository_id": "repo-1",
        "source_event_id": "release-1", "source_event_type": "release", "status": "draft",
        "brief": "A useful release", "source_evidence": [{"source_id": "doc-1"}],
        "source_feedback_ids": [], "source_gap_id": None, "workflow_run_id": "run-1",
        "created_at": None, "updated_at": None,
    })
    calls: list[tuple[str, str, tuple[Any, ...]]] = field(default_factory=list)

    async def fetch_one(self, query: str, *args: Any) -> Any:
        self.calls.append(("fetch_one", query, args))
        return self.package_row

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append(("execute", query, args))
        return "OK"

    async def fetch_all(self, query: str, *args: Any) -> list[Any]:
        self.calls.append(("fetch_all", query, args))
        return []


def request() -> ContentRequest:
    return ContentRequest(
        org_id="org-1", repository_id="repo-1", source_event_id="release-1",
        source_event_type="release", source_title="Version 1.2",
        source_summary="A useful release", source_evidence=[{"source_id": "doc-1"}],
        requested_channels=["blog", "linkedin", "x"], audience="developers", tone="clear",
    )


async def test_generation_persists_all_requested_channels_and_enters_review() -> None:
    db = FakeDb()
    state = await run_content_generation(
        request(), repository=ContentRepository(cast(DatabaseClient, db)), run_id="run-1"
    )

    assert state.status is WorkflowStatus.PENDING_REVIEW
    assert state.result["status"] == "in_review"
    assert {item["channel"] for item in state.result["variants"]} == {"blog", "linkedin", "x"}
    assert state.result["source_feedback_ids"] == []
    assert sum("content_variants" in query for _, query, _ in db.calls) == 4


async def test_generation_persists_uuid_variant_ids() -> None:
    db = FakeDb()
    state = await run_content_generation(
        request(), repository=ContentRepository(cast(DatabaseClient, db)), run_id="run-1"
    )
    assert state.status is WorkflowStatus.PENDING_REVIEW
    ids = [args[0] for method, query, args in db.calls
           if method == "execute" and "INSERT INTO content_variants" in query]
    assert len(ids) == 3
    assert len({UUID(value) for value in ids}) == 3


async def test_graph_persists_uuid_variant_ids() -> None:
    from draftly.integrations.strands import graph  # noqa: F401
    from draftly.orchestration.graphs.content_graph import ContentPersistNode

    db = FakeDb()
    event = request().model_dump(mode="json")
    payload = json.dumps({"title": "Version 1.2", "body": "A useful release"})
    task = [{"text": f"Original Task: {json.dumps(event)}\nInputs from previous nodes:\n"
             + "\n".join(f"From content_{channel}:\n  - Agent: {payload}"
                         for channel in ("blog", "linkedin", "x"))}]
    await ContentPersistNode(ContentRepository(cast(DatabaseClient, db))).invoke_async(task)
    ids = [args[0] for method, query, args in db.calls
           if method == "execute" and "INSERT INTO content_variants" in query]
    assert len(ids) == 3
    assert len({UUID(value) for value in ids}) == 3
