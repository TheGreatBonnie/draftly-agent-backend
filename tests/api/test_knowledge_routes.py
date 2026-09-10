"""Knowledge read endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes.knowledge import router

ITEM = {
    "id": "11111111-1111-1111-1111-111111111111",
    "org_id": "org-1",
    "namespace": "knowledge",
    "memory_type": "knowledge",
    "content": "authly.tokens attribute provides access to TokenService",
    "summary": None,
    "status": "active",
    "importance": 0.5,
    "confidence": 0.9,
    "version": 1,
    "access_count": 3,
    "last_accessed_at": "2026-08-30T12:00:00Z",
    "created_at": "2026-08-29T12:00:00Z",
    "updated_at": "2026-08-30T12:00:00Z",
}

ITEM_LOW_CONF = {**ITEM, "id": "22222222-2222-2222-2222-222222222222", "confidence": 0.4}


class FakeMemoryRepo:
    def __init__(self, items: list[dict[str, Any]]):
        self._items = items

    async def list_namespace(
        self, *, namespace: str, org_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [i for i in self._items if org_id is None or i.get("org_id") == org_id]

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        return next((i for i in self._items if i["id"] == memory_id), None)


class FakeKnowledgeRepo:
    def __init__(
        self,
        memory: FakeMemoryRepo,
        sources: Any = None,
        links: Any = None,
        feedback: Any = None,
        search_result: list[dict[str, Any]] | None = None,
    ) -> None:
        self.memory = memory
        self.sources = sources
        self.links = links
        self.feedback = feedback
        self.search_result = search_result

    @staticmethod
    def _item(record: dict[str, Any]) -> dict[str, Any]:
        confidence = float(record.get("confidence") or 0.0)
        status = (
            "stale"
            if record.get("status") not in ("active", None)
            else "verified"
            if confidence >= 0.5
            else "needs-verification"
        )
        return {
            "id": record["id"],
            "entity": record.get("summary") or record.get("content"),
            "description": record.get("summary"),
            "status": status,
            "importance": record.get("importance"),
            "confidence": record.get("confidence"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "namespace": record.get("namespace", "knowledge"),
            "memory_type": record.get("memory_type", "knowledge"),
        }

    async def list_page(self, *, org_id, status=None, limit=25, cursor=None):
        records = await self.memory.list_namespace(namespace="knowledge", org_id=org_id)
        items = [self._item(record) for record in records]
        if status:
            items = [item for item in items if item["status"] == status]
        return {"items": items[:limit], "total": len(items), "next_cursor": None}

    async def stats(self, *, org_id):
        page = await self.list_page(org_id=org_id)
        return {
            "total": page["total"],
            "verified": sum(i["status"] == "verified" for i in page["items"]),
            "needs_verification": sum(
                i["status"] == "needs-verification" for i in page["items"]
            ),
            "stale": sum(i["status"] == "stale" for i in page["items"]),
        }

    async def search(self, *, org_id, query, limit=20, status=None):
        if self.search_result is not None:
            items = self.search_result[:limit]
        else:
            records = await self.memory.list_namespace(namespace="knowledge", org_id=org_id)
            items = [self._item(record) for record in records[:limit]]
        if status:
            items = [item for item in items if item["status"] == status]
        return items

    async def detail(self, *, org_id, item_id):
        record = await self.memory.get(memory_id=item_id)
        if (
            record is None
            or record.get("org_id") != org_id
            or record.get("namespace") != "knowledge"
        ):
            return None
        item = self._item(record)
        item.pop("namespace", None)
        item.pop("memory_type", None)
        item["description"] = record.get("content")
        item["sources"] = []
        item["related"] = []
        item["feedback"] = []
        if self.sources:
            item["sources"] = await self.sources.list_by_memory(
                org_id=org_id, memory_item_id=item_id
            )
        if self.links:
            item["related"] = await self.links.list_by_memory(
                org_id=org_id, memory_item_id=item_id
            )
        if self.feedback:
            item["feedback"] = await self.feedback.list_by_memory(
                org_id=org_id, memory_item_id=item_id
            )
        return item

    async def source_summaries(self, *, org_id):
        return []

    async def graph(self, *, org_id, limit_nodes=100, limit_edges=200):
        return {"nodes": [], "edges": []}

    async def topics(self, *, org_id, limit=20):
        return []

    async def embedding_stats(self, *, org_id):
        return {
            "total_items": 0,
            "embedded_items": 0,
            "coverage_percent": 0.0,
            "models": [],
            "last_embedded_at": None,
        }


def make_app(
    items: list[dict[str, Any]] | None = None,
    token: dict[str, Any] | None = None,
) -> FastAPI:
    memory = FakeMemoryRepo(items or [ITEM, ITEM_LOW_CONF])
    repos = SimpleNamespace(memory=memory, knowledge=FakeKnowledgeRepo(memory))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: (
        token if token is not None else {"org_id": "org-1"}
    )
    app.state.draftly = SimpleNamespace(dependencies=SimpleNamespace(repositories=repos))
    return app


def test_list_knowledge_maps_status() -> None:
    client = TestClient(make_app())
    resp = client.get("/knowledge")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    by_id = {i["id"]: i for i in items}
    assert by_id[ITEM["id"]]["status"] == "verified"
    assert by_id[ITEM_LOW_CONF["id"]]["status"] == "needs-verification"


def test_list_is_scoped_to_org() -> None:
    # A foreign-org item must not leak into the token org's list.
    foreign = {**ITEM, "id": "33333333-3333-3333-3333-333333333333", "org_id": "org-2"}
    client = TestClient(make_app([ITEM, foreign]))
    resp = client.get("/knowledge")
    items = resp.json()["items"]
    assert all(i["id"] != foreign["id"] for i in items)
    assert len(items) == 1


def test_list_filters_by_status() -> None:
    client = TestClient(make_app())
    resp = client.get("/knowledge", params={"status": "verified"})
    body = resp.json()
    assert all(i["status"] == "verified" for i in body["items"])


def test_missing_org_id_is_rejected() -> None:
    response = TestClient(make_app(token={})).get("/knowledge")
    assert response.status_code in (400, 403)


def test_list_limit_is_bounded() -> None:
    response = TestClient(make_app()).get("/knowledge", params={"limit": 101})
    assert response.status_code == 422


def test_get_unknown_returns_404() -> None:
    client = TestClient(make_app(items=[]))
    resp = client.get("/knowledge/99999999-9999-9999-9999-999999999999")
    assert resp.status_code == 404


def test_invalid_item_id_is_rejected() -> None:
    response = TestClient(make_app()).get("/knowledge/not-a-uuid")
    assert response.status_code == 400


def test_stats_counts_by_derived_status() -> None:
    client = TestClient(make_app())
    resp = client.get("/knowledge/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["verified"] == 1
    assert body["needs_verification"] == 1
    assert body["stale"] == 0


class FakeSearchMemoryRepo(FakeMemoryRepo):
    def __init__(self, items, search_result):
        super().__init__(items)
        self._search_result = search_result

    async def semantic_search(self, *, namespace, embedding, limit, org_id=None):
        return self._search_result


def make_search_app() -> FastAPI:
    class FakeEmbedService:
        async def embed(self, text: str) -> list[float]:
            return [0.1, 0.2]

    memory = FakeSearchMemoryRepo([ITEM], [])
    repo = FakeKnowledgeRepo(memory, search_result=[])
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-1"}
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(repositories=SimpleNamespace(memory=memory, knowledge=repo)),
        embeddings=FakeEmbedService(),
    )
    return app


def test_search_returns_items() -> None:
    client = TestClient(make_search_app())
    resp = client.get("/knowledge/search", params={"q": "tokens"})
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_search_requires_query() -> None:
    client = TestClient(make_search_app())
    resp = client.get("/knowledge/search", params={"q": "   "})
    assert resp.status_code == 400


def test_get_item_includes_sources_and_related() -> None:
    repo = FakeMemoryRepo([ITEM])

    class FakeSources:
        async def list_by_memory(self, *, org_id, memory_item_id):
            return [{
                "id": "source-1",
                "source_type": "spec",
                "source_url": "https://x",
                "evidence": "e",
            }]

    class FakeLinks:
        async def list_by_memory(self, *, org_id, memory_item_id):
            return [{
                "id": "link-1",
                "relationship": "related",
                "source_memory_id": memory_item_id,
                "target_memory_id": "abc",
            }]

    class FakeFeedback:
        async def list_by_memory(self, *, org_id, memory_item_id):
            return [{
                "id": "feedback-1",
                "feedback_type": "verified",
                "score": 1.0,
                "source": "FactChecker Agent",
            }]

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-1"}
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(
            repositories=SimpleNamespace(
                memory=repo,
                knowledge=FakeKnowledgeRepo(repo, FakeSources(), FakeLinks(), FakeFeedback()),
                memory_sources=FakeSources(),
                memory_links=FakeLinks(),
                memory_feedback=FakeFeedback(),
            )
        )
    )
    client = TestClient(app)
    resp = client.get(f"/knowledge/{ITEM['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sources"][0]["source_type"] == "spec"
    assert body["related"][0]["relationship"] == "related"
    assert body["feedback"][0]["feedback_type"] == "verified"
