"""Episodic memory service tests."""

import pytest

from draftly.memory.episodic.service import EpisodicService
from tests.fakes.memory_stores import FakeEpisodesStore


class StaticEmbedder:
    async def embed(self, text: str) -> list[float]:
        return [0.5, 0.5]


@pytest.mark.asyncio
async def test_record_episode_persists_and_embeds_summary():
    store = FakeEpisodesStore()
    svc = EpisodicService(store=store, embeddings=StaticEmbedder())
    record = await svc.record_episode(
        org_id="org1",
        agent_run_id=None,
        trigger_type="github_pr",
        trigger_id="482",
        trigger_summary="Token expiry changed",
        actions_taken=["researched repo"],
        tools_used=["get_diff"],
        outcome="success",
        evaluation_results={"pass": True},
        artifacts_created=["docs/auth/tokens.md"],
    )
    assert record["id"]
    assert store.rows[0]["trigger_type"] == "github_pr"
    assert store.rows[0]["embedding"] == [0.5, 0.5]


@pytest.mark.asyncio
async def test_find_similar_returns_matches():
    svc = EpisodicService(store=FakeEpisodesStore(), embeddings=StaticEmbedder())
    await svc.record_episode(
        org_id="org1",
        agent_run_id=None,
        trigger_type="github_pr",
        trigger_id="1",
        trigger_summary="oauth token change",
        actions_taken=[],
        tools_used=[],
        outcome="success",
        evaluation_results=None,
        artifacts_created=[],
    )
    hits = await svc.find_similar("token change", org_id="org1")
    assert len(hits) == 1
    assert hits[0]["trigger_summary"] == "oauth token change"


@pytest.mark.asyncio
async def test_find_similar_fails_open():
    class Boom:
        async def search(self, **kw):
            raise RuntimeError("db down")

    svc = EpisodicService(store=Boom(), embeddings=StaticEmbedder())
    assert await svc.find_similar("x") == []


def test_normalize_vector_pads_to_1536():
    from draftly.memory.vector_utils import normalize_vector

    out = normalize_vector([1.0, 2.0])
    assert len(out) == 1536 and out[:2] == [1.0, 2.0] and out[-1] == 0.0
    assert len(normalize_vector([0.0] * 2000)) == 1536
