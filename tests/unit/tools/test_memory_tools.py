"""Curator tool tests."""

import json

import pytest

from tests.fakes.memory_stores import FakeDocGraphStore, FakeSemanticRepo


@pytest.mark.asyncio
async def test_supersede_tool_reports_result(monkeypatch):
    from draftly.tools.memory import curation

    repo = FakeSemanticRepo()
    repo.seed("old-1", content="old fact")

    class FakeMemoryService:
        def __init__(self):
            self.repository = repo

        async def supersede(self, *a, **kw):
            return {"id": "new-9"}

    monkeypatch.setattr(curation, "_memory_service", lambda: FakeMemoryService())
    out = await curation.supersede_memory(
        "old-1",
        "tokens expire in 1h",
        "knowledge",
        "org1",
        evidence_json=json.dumps(["a.py"]),
    )
    payload = json.loads(out)
    assert payload["superseded"] is True and payload["new_id"] == "new-9"


@pytest.mark.asyncio
async def test_reinforce_tool_bumps_confidence(monkeypatch):
    from draftly.tools.memory import curation

    repo = FakeSemanticRepo()
    repo.seed("m1", content="fact")

    class FakeMemoryService:
        def __init__(self):
            self.repository = repo

        async def repository_get(self, memory_id):
            return await self.repository.get(memory_id)

    svc = FakeMemoryService()

    class RepoProxy:
        def __init__(self, inner):
            self.inner = inner

        async def get(self, memory_id):
            return await self.inner.get(memory_id)

        async def update(self, memory_id, **fields):
            self.inner.rows[memory_id].update(fields)
            return dict(self.inner.rows[memory_id])

    svc.repository = RepoProxy(repo)
    monkeypatch.setattr(curation, "_memory_service", lambda: svc)

    out = await curation.reinforce_memory("m1", amount=0.2)
    assert json.loads(out)["ok"] is True
    assert repo.rows["m1"]["confidence"] == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_record_doc_relation_writes_edge(monkeypatch):
    from draftly.tools.memory import knowledge

    store = FakeDocGraphStore()

    class FakeGraph:
        def __init__(self):
            self.store = store

        async def link(self, *a, **kw):
            return {"id": "e1"}

    monkeypatch.setattr(knowledge, "_docgraph_service", lambda: FakeGraph())
    out = await knowledge.record_doc_relation(
        "a.py", "docs/a.md", "DOCUMENTED_BY", "org1"
    )
    assert json.loads(out)["linked"] is True


@pytest.mark.asyncio
async def test_memory_search_delegates_to_service(monkeypatch):
    from draftly.tools.memory import search as memory_search_mod

    calls = {}

    class FakeService:
        async def recall(self, *, namespace, query, limit):
            calls.update(namespace=namespace, query=query, limit=limit)
            return [{"id": "m1"}]

    monkeypatch.setattr(memory_search_mod, "_memory_service", lambda: FakeService())
    out = await memory_search_mod.memory_search("knowledge", "oauth", limit=3)
    assert out[0]["id"] == "m1"
    assert calls["limit"] == 3
