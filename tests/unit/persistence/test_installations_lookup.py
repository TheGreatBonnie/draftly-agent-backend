"""Unit tests for org-scoped GitHub installation lookup."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, cast

from draftly.integrations.database.client import DatabaseClient
from draftly.persistence.repositories.github import GitHubInstallationsRepository


@dataclass
class FakeDb:
    rows: list[Any] = field(default_factory=list)
    queries: list[tuple[str, tuple]] = field(default_factory=list)

    async def fetch_all(self, query: str, *args: Any) -> list[Any]:
        self.queries.append((" ".join(query.split()), args))
        return self.rows


async def test_list_by_org_filters_on_org_and_parses_repositories_json():
    db = FakeDb(rows=[
        {
            "id": "gi-1",
            "installation_id": 12345,
            "github_org": "acme",
            "repositories": '["repo-a"]',  # stored as TEXT JSON
            "created_at": None,
            "updated_at": None,
        }
    ])
    repo = GitHubInstallationsRepository(db=cast(DatabaseClient, db))

    installs = await repo.list_by_org("org_clerk_123")

    assert installs[0]["installation_id"] == 12345
    assert installs[0]["repositories"] == ["repo-a"]
    sql, args = db.queries[-1]
    assert "FROM github_installations" in sql
    assert "org_id = $1" in sql
    assert args[0] == "org_clerk_123"


async def test_first_for_org_returns_none_when_empty():
    repo = GitHubInstallationsRepository(db=cast(DatabaseClient, FakeDb()))
    assert await repo.first_for_org("missing-org") is None


async def test_repository_dependencies_declares_installations_field():
    from draftly.app.dependencies import RepositoryDependencies

    params = inspect.signature(RepositoryDependencies.__init__).parameters
    assert "github_installations" in params
