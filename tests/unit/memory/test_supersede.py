"""Supersede lifecycle tests for MemoryService."""

import pytest

from draftly.memory.service import MemoryService
from tests.fakes.memory_stores import FakeSemanticRepo


@pytest.mark.asyncio
async def test_supersede_marks_old_and_creates_replacement():
    repo = FakeSemanticRepo()
    repo.seed("old-1", content="tokens expire after 24h")
    svc = MemoryService(repository=repo)

    new = await svc.supersede(
        "old-1",
        "Access tokens expire after 1 hour.",
        namespace="knowledge",
        org_id="org1",
        evidence=["auth/token_service.py"],
    )

    assert new is not None and new["status"] == "active"
    old = await repo.get("old-1")
    assert old["status"] == "superseded"
    assert any(
        s["source_type"] == "supersedes" and s["source_id"] == "old-1"
        for s in repo.sources[new["id"]]
    )


@pytest.mark.asyncio
async def test_supersede_missing_old_returns_none():
    svc = MemoryService(repository=FakeSemanticRepo())
    assert await svc.supersede("nope", "x", namespace="knowledge") is None
