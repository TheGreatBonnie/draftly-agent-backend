from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from draftly.content.models import ContentPackage, ContentPackageStatus
from draftly.integrations.database.client import DatabaseClient
from draftly.persistence.repositories.content import ContentRepository


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

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append(("execute", query, args))
        return "OK"


def row() -> dict[str, Any]:
    return {
        "package_id": "package-1",
        "org_id": "org-1",
        "repository_id": "repo-1",
        "source_event_id": "release-1",
        "source_event_type": "release",
        "status": "draft",
        "brief": "A release",
        "source_evidence": [{"source_id": "doc-1"}],
        "source_feedback_ids": [],
        "source_gap_id": None,
        "workflow_run_id": "run-1",
        "created_at": None,
        "updated_at": None,
    }


async def test_create_or_get_is_idempotent_and_org_scoped() -> None:
    db = FakeDb(responses=[row()])
    repo = ContentRepository(database=cast(DatabaseClient, db))
    package = ContentPackage(
        id="package-1", org_id="org-1", repository_id="repo-1",
        source_event_id="release-1", source_event_type="release",
        status=ContentPackageStatus.DRAFT, brief="A release",
        source_evidence=[{"source_id": "doc-1"}], workflow_run_id="run-1",
    )

    saved = await repo.create_or_get(package)

    assert saved.id == "package-1"
    assert (
        "ON CONFLICT (org_id, repository_id, source_event_type, source_event_id)"
        in db.calls[0][1]
    )
    assert db.calls[0][2][1] == "org-1"


async def test_get_filters_by_org_and_package() -> None:
    db = FakeDb(responses=[row()])
    repo = ContentRepository(database=cast(DatabaseClient, db))

    package = await repo.get(org_id="org-1", package_id="package-1")

    assert package is not None
    assert db.calls[0][2] == ("org-1", "package-1")


@pytest.mark.parametrize("operation", ["get", "list", "create_or_get"])
async def test_package_reads_preserve_revision_package_id(operation: str) -> None:
    revision = {
        "id": "aab28994-a3b1-42bc-b72b-6e13fded1d58",
        "package_id": "package-1",
        "revision_number": 1,
        "reason": "initial",
        "reviewer_comment": None,
        "created_by_run_id": "run-1",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    db = FakeDb(responses=[[row()] if operation == "list" else row(), [], [revision]])
    repo = ContentRepository(database=cast(DatabaseClient, db))
    if operation == "list":
        packages = await repo.list(org_id="org-1")
        assert len(packages) == 1
        package = packages[0]
    elif operation == "create_or_get":
        package = await repo.create_or_get(repo._row_to_package(row()))
    else:
        package = await repo.get(org_id="org-1", package_id="package-1")
    assert package is not None
    assert len(package.revisions) == 1
    assert package.revisions[0].model_dump() == revision
