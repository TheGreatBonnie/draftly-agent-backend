"""Persistence contract tests for feedback signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from draftly.feedback.models import FeedbackItem
from draftly.integrations.database.client import DatabaseClient
from draftly.persistence.repositories.feedback import FeedbackRepository


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

    async def fetch_all(self, query: str, *args: Any) -> list[Any]:
        self.calls.append(("fetch_all", query, args))
        return self.responses.pop(0) if self.responses else []


def _item(**overrides: Any) -> FeedbackItem:
    values = {
        "id": "signal-1",
        "org_id": "org-a",
        "platform": "github",
        "source_event_id": "issue-comment-1",
        "source_url": "https://github.com/acme/repo/issues/1#issuecomment-1",
        "content": "How do I configure this?",
        "topic": "configuration",
    }
    values.update(overrides)
    return FeedbackItem(**values)


async def test_save_signal_uses_org_platform_event_id_upsert() -> None:
    db = FakeDb(
        responses=[
            {
                "id": "signal-1",
                "org_id": "org-a",
                "platform": "github",
                "source_event_id": "issue-comment-1",
                "source_url": "https://github.com/acme/repo/issues/1#issuecomment-1",
                "content": "How do I configure this?",
                "topic": "configuration",
                "category": "question",
                "sentiment": "neutral",
            }
        ]
    )
    repo = FeedbackRepository(database=cast(DatabaseClient, db))

    result = await repo.save_signal(_item())

    assert result.id == "signal-1"
    _kind, query, args = db.calls[0]
    assert "ON CONFLICT (org_id, platform, source_event_id)" in query
    assert args[:3] == ("org-a", "github", "issue-comment-1")


async def test_list_recent_is_organization_scoped() -> None:
    db = FakeDb(responses=[[]])
    repo = FeedbackRepository(database=cast(DatabaseClient, db))

    await repo.list_recent("org-a")

    _kind, query, args = db.calls[-1]
    assert "org_id = $1" in query
    assert args[0] == "org-a"


async def test_list_org_ids_reads_organizations_for_scoped_scheduling() -> None:
    db = FakeDb(responses=[[{"clerk_org_id": "org-a"}, {"clerk_org_id": "org-b"}]])
    repo = FeedbackRepository(database=cast(DatabaseClient, db))

    assert await repo.list_org_ids() == ["org-a", "org-b"]
