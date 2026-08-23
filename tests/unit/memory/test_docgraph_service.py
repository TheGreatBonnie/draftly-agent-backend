"""DocGraph service tests."""

import pytest

from draftly.memory.docgraph.service import DocGraphService
from tests.fakes.memory_stores import FakeDocGraphStore


@pytest.mark.asyncio
async def test_link_is_idempotent_and_confirms():
    store = FakeDocGraphStore()
    svc = DocGraphService(store=store)
    await svc.link("a.py", "docs/a.md", "DOCUMENTED_BY", org_id="org1")
    first = await svc.link(
        "a.py", "docs/a.md", "DOCUMENTED_BY", org_id="org1", evidence=["pr#482"]
    )
    assert len(store.edges) == 1  # no duplicate edge
    assert "pr#482" in str(first["evidence"])


@pytest.mark.asyncio
async def test_affected_docs_traverses_graph():
    svc = DocGraphService(store=FakeDocGraphStore())
    await svc.link(
        "auth/token_service.py", "Token Lifecycle", "IMPLEMENTS",
        org_id="org1", source_type="code", target_type="concept",
    )
    await svc.link(
        "Token Lifecycle", "docs/auth/tokens.md", "DOCUMENTED_BY",
        org_id="org1", source_type="concept", target_type="doc",
    )
    docs = await svc.affected_docs(["auth/token_service.py"], org_id="org1")
    assert [d["key"] for d in docs] == ["docs/auth/tokens.md"]
