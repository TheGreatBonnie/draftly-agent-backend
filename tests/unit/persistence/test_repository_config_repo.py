"""Unit tests for repository config repository."""

from dataclasses import dataclass, field
from typing import Any

import pytest

from draftly.persistence.repositories.repository_config import RepositoryConfigRepository


@dataclass
class FakeClient:
    responses: list[Any] = field(default_factory=list)
    calls: list[Any] = field(default_factory=list)

    async def fetch_one(self, query, *args):
        self.calls.append(("fetch_one", query, args))
        return self.responses.pop(0) if self.responses else None

    async def fetch_all(self, query, *args):
        self.calls.append(("fetch_all", query, args))
        return self.responses.pop(0) if self.responses else []

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))
        if self.responses:
            self.responses.pop(0)
        return "OK"


@pytest.mark.asyncio
async def test_get_returns_none_when_not_found():
    client = FakeClient(responses=[None])
    repo = RepositoryConfigRepository(client)
    result = await repo.get("org-123", "owner/repo")
    assert result is None


@pytest.mark.asyncio
async def test_list_by_org_returns_repos():
    client = FakeClient(responses=[
        [{"full_name": "owner/repo1"}, {"full_name": "owner/repo2"}]
    ])
    repo = RepositoryConfigRepository(client)
    result = await repo.list_by_org("org-123")
    assert len(result) == 2


@pytest.mark.asyncio
async def test_upsert_inserts_new_repository():
    client = FakeClient(responses=[None, "OK"])
    repo = RepositoryConfigRepository(client)
    await repo.upsert("org-123", "owner/repo")
    assert len(client.calls) == 2
