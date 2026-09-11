"""Documentation read endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes.documentation import router
from draftly.persistence.repositories.document_revisions import (
    DocumentationRevision,
    RevisionConflict,
    RevisionPage,
)

DOC = {
    "id": "11111111-1111-1111-1111-111111111111",
    "org_id": "org-1",
    "repository": "acme/repo",
    "path": "docs/index.md",
    "title": "ACME Docs",
    "content": "# ACME\n\nHello world.",
    "document_type": "general",
    "version": 1,
    "status": "indexed",
    "metadata": {
        "branch": "master",
        "source_url": "https://github.com/acme/repo/blob/master/docs/index.md",
    },
    "stale": False,
    "outdated": False,
    "incomplete": False,
    "broken_links": False,
    "unsupported_claims": False,
    "created_at": "2026-08-29T12:00:00Z",
    "updated_at": "2026-08-30T12:00:00Z",
}


class FakeDocsRepo:
    def __init__(self, items: list[dict[str, Any]]):
        self._items = items
        self.last_projection: dict[str, Any] = {}

    async def list_by_org(
        self, *, org_id: str, limit: int = 1000
    ) -> list[dict[str, Any]]:
        return [i for i in self._items if i.get("org_id") == org_id]

    async def get(self, document_id: str) -> dict[str, Any] | None:
        return next((i for i in self._items if i["id"] == document_id), None)

    async def get_for_org(self, *, document_id: str, org_id: str) -> dict[str, Any] | None:
        return next(
            (i for i in self._items if i["id"] == document_id and i.get("org_id") == org_id),
            None,
        )

    async def list_projection_by_org(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.last_projection = kwargs
        return [
            {key: value for key, value in item.items() if key != "content"}
            for item in await self.list_by_org(org_id=kwargs["org_id"], limit=kwargs["limit"])
        ]


class FakeRevisionRepo:
    def __init__(self) -> None:
        self.revision = DocumentationRevision(
            id="rev-1",
            document_id=DOC["id"],
            org_id="org-1",
            revision_number=1,
            origin="manual",
            status="draft",
            title="Draft title",
            content="# Draft",
            base_source_hash="old-sha",
            created_by="user-1",
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        )

    async def create_draft(self, **kwargs: Any) -> DocumentationRevision:
        if kwargs["base_source_hash"] == "old-sha":
            raise RevisionConflict(
                current_source_hash="new-sha",
                current_revision_id=None,
            )
        return self.revision

    async def list_revisions(self, **kwargs: Any) -> RevisionPage:
        return RevisionPage(items=[self.revision], total=1, next_cursor=None)

    async def restore_revision(self, **kwargs: Any) -> DocumentationRevision:
        return self.revision


class FakeEvaluationRepo:
    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"id": "eval-1", "target_id": kwargs.get("target_id"), "score": 80.0}]

    async def create(self, **kwargs: Any) -> dict[str, Any]:
        return {"id": "eval-1", **kwargs}


def make_app(items: list[dict[str, Any]] | None = None) -> FastAPI:
    repos = SimpleNamespace(
        documents=FakeDocsRepo(items or [DOC]),
        revisions=FakeRevisionRepo(),
        evaluations=FakeEvaluationRepo(),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-1"}
    app.state.draftly = SimpleNamespace(dependencies=SimpleNamespace(repositories=repos))
    return app


def test_list_documentation_is_scoped_to_org() -> None:
    foreign = {**DOC, "id": "33333333-3333-3333-3333-333333333333", "org_id": "org-2"}
    client = TestClient(make_app([DOC, foreign]))
    resp = client.get("/documentation")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(i["id"] != foreign["id"] for i in items)
    assert len(items) == 1


def test_stats_counts_by_status() -> None:
    stale = {**DOC, "id": "22222222-2222-2222-2222-222222222222", "status": "stale"}
    client = TestClient(make_app([DOC, stale]))
    resp = client.get("/documentation/stats")
    body = resp.json()
    assert body["total"] == 2
    assert body["by_status"]["published"] == 1  # "indexed" maps to "published"
    assert body["by_status"]["stale"] == 1


def test_get_unknown_returns_404() -> None:
    client = TestClient(make_app(items=[]))
    resp = client.get("/documentation/99999999-9999-9999-9999-999999999999")
    assert resp.status_code == 404


def test_get_is_scoped_to_org() -> None:
    foreign = {**DOC, "id": "44444444-4444-4444-4444-444444444444", "org_id": "org-2"}
    client = TestClient(make_app([DOC, foreign]))
    resp = client.get(f"/documentation/{foreign['id']}")
    assert resp.status_code == 404


def test_list_returns_projection_total_without_content() -> None:
    client = TestClient(make_app())

    resp = client.get("/documentation?limit=25")

    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert "content" not in resp.json()["items"][0]


def test_list_forwards_search_query_to_projection() -> None:
    app = make_app()
    client = TestClient(app)

    response = client.get("/documentation?query=oauth")

    assert response.status_code == 200
    assert app.state.draftly.dependencies.repositories.documents.last_projection["query"] == "oauth"


def test_save_revision_returns_conflict_details() -> None:
    client = TestClient(make_app())

    resp = client.post(
        f"/documentation/{DOC['id']}/revisions",
        json={"content": "# Draft", "base_source_hash": "old-sha"},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "DOCUMENT_CONFLICT"
    assert resp.json()["detail"]["current_source_hash"] == "new-sha"


def test_history_is_organization_scoped_and_typed() -> None:
    client = TestClient(make_app())

    resp = client.get(f"/documentation/{DOC['id']}/history")

    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["revision_number"] == 1


def test_run_document_evaluation_persists_targeted_result() -> None:
    client = TestClient(make_app())

    resp = client.post(f"/documentation/{DOC['id']}/evaluations")

    assert resp.status_code == 200
    assert resp.json()["evaluation"]["target_id"] == DOC["id"]
    assert resp.json()["evaluation"]["evaluation_type"] == "documentation"


def test_list_document_evaluations_is_targeted() -> None:
    client = TestClient(make_app())

    resp = client.get(f"/documentation/{DOC['id']}/evaluations")

    assert resp.status_code == 200
    assert resp.json()["items"][0]["target_id"] == DOC["id"]
