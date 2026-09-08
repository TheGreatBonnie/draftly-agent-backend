"""Persistence contract tests for feedback outcomes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from draftly.integrations.database.client import DatabaseClient
from draftly.persistence.repositories.feedback_outcomes import FeedbackOutcomeRepository


@dataclass
class FakeDb:
    responses: list[Any] = field(default_factory=list)
    calls: list[tuple[str, str, tuple[Any, ...]]] = field(default_factory=list)

    async def fetch_one(self, query: str, *args: Any) -> Any:
        self.calls.append(("fetch_one", query, args))
        return self.responses.pop(0) if self.responses else None

    async def fetch_all(self, query: str, *args: Any) -> list[Any]:
        self.calls.append(("fetch_all", query, args))
        return self.responses.pop(0) if self.responses else []


async def test_save_outcome_is_idempotent_by_source() -> None:
    db = FakeDb(responses=[{"outcome_id": "outcome-1"}])
    repo = FeedbackOutcomeRepository(database=cast(DatabaseClient, db))

    outcome_id = await repo.save_outcome(
        "org-a", "evaluation", "run-1", {"status": "failed"}
    )

    assert outcome_id == "outcome-1"
    assert "ON CONFLICT (org_id, source_type, source_id)" in db.calls[0][1]


async def test_save_outcome_serializes_nested_datetimes() -> None:
    db = FakeDb(responses=[{"outcome_id": "outcome-1"}])
    repo = FeedbackOutcomeRepository(database=cast(DatabaseClient, db))

    await repo.save_outcome(
        "org-a",
        "evaluation",
        "run-1",
        {
            "status": "completed",
            "evaluation": {"id": "e-1", "created_at": datetime.now(UTC)},
        },
    )

    payload = json.loads(db.calls[0][2][3])
    assert payload["status"] == "completed"
    assert payload["evaluation"]["created_at"]


async def test_list_unresolved_is_organization_scoped() -> None:
    db = FakeDb(responses=[[{"outcome_id": "outcome-1", "org_id": "org-a"}]])
    repo = FeedbackOutcomeRepository(database=cast(DatabaseClient, db))

    rows = await repo.list_unresolved("org-a")

    assert rows[0]["org_id"] == "org-a"
    assert db.calls[0][2][0] == "org-a"
