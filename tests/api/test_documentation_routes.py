"""Documentation read endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes.documentation import router

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

    async def list_by_org(
        self, *, org_id: str, limit: int = 1000
    ) -> list[dict[str, Any]]:
        return [i for i in self._items if i.get("org_id") == org_id]

    async def get(self, document_id: str) -> dict[str, Any] | None:
        return next((i for i in self._items if i["id"] == document_id), None)


def make_app(items: list[dict[str, Any]] | None = None) -> FastAPI:
    repos = SimpleNamespace(documents=FakeDocsRepo(items or [DOC]))
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
