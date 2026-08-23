"""Procedural memory service tests."""

import pytest

from draftly.memory.procedural.service import ProceduralService
from tests.fakes.memory_stores import FakeProceduresStore


class StaticEmbedder:
    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]


def make_svc() -> ProceduralService:
    return ProceduralService(store=FakeProceduresStore(), embeddings=StaticEmbedder())


@pytest.mark.asyncio
async def test_reinforce_increases_confidence_and_success():
    svc = make_svc()
    proc = await svc.create("auth-playbook", "inspect tokens first", org_id="org1")
    updated = await svc.reinforce(proc["id"])
    assert updated["success_count"] == 1
    assert updated["confidence"] > 0.5
    assert updated["status"] == "active"


@pytest.mark.asyncio
async def test_low_confidence_after_three_applications_archives():
    svc = make_svc()
    proc = await svc.create("p", "d", org_id="org1")
    for _ in range(3):
        proc = await svc.invalidate(proc["id"])
    assert proc["status"] == "archived"
    assert proc["failure_count"] == 3


@pytest.mark.asyncio
async def test_match_excludes_archived_and_other_orgs():
    svc = make_svc()
    proc = await svc.create("p", "playbook about oauth", org_id="org1")
    await svc.store.update(proc["id"], status="archived")
    other = await svc.create("q", "playbook about oauth too", org_id="org2")
    hits = await svc.match("oauth", org_id="org2")
    assert len(hits) == 1 and hits[0]["id"] == other["id"]
