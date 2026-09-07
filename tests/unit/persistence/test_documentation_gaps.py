"""Persistence contract tests for documentation gap candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from draftly.feedback.models import DocumentationGapCandidate
from draftly.integrations.database.client import DatabaseClient
from draftly.persistence.repositories.documentation_gaps import DocumentationGapRepository


@dataclass
class FakeDb:
    responses: list[Any] = field(default_factory=list)
    calls: list[tuple[str, str, tuple[Any, ...]]] = field(default_factory=list)

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append(("execute", query, args))
        return "OK"

    async def fetch_one(self, query: str, *args: Any) -> Any:
        self.calls.append(("fetch_one", query, args))
        return self.responses.pop(0) if self.responses else None


async def test_upsert_candidate_returns_durable_gap_id() -> None:
    db = FakeDb(responses=[{"gap_id": "gap-1"}])
    repo = DocumentationGapRepository(database=cast(DatabaseClient, db))
    candidate = DocumentationGapCandidate(topic="retries", occurrences=3)

    gap_id = await repo.upsert_candidate("org-a", candidate)

    assert gap_id == "gap-1"
    assert "ON CONFLICT (org_id, topic)" in db.calls[0][1]


async def test_mark_dispatched_records_route_and_run() -> None:
    db = FakeDb()
    repo = DocumentationGapRepository(database=cast(DatabaseClient, db))

    await repo.mark_dispatched("gap-1", "run-1", route="documentation")

    _kind, query, args = db.calls[0]
    assert "dispatched_at" in query
    assert args == ("gap-1", "run-1", "documentation")
