"""Grounding multi-source merge tests."""

import pytest

from draftly.agents.shared.memory_grounding import MemoryGroundedNode


class FakeInner:
    name = "inner"

    def __init__(self):
        self.seen = None

    async def invoke_async(self, task, invocation_state=None, **kw):
        self.seen = task


class Bundle:
    def __init__(self, knowledge, episodes, procedures):
        self.knowledge = knowledge
        self.episodes = episodes
        self.procedures = procedures


@pytest.mark.asyncio
async def test_ground_merges_three_sources():
    async def knowledge(q, limit=5):
        return [{"content": "PKCE is required"}]

    async def episodes(q, limit=5):
        return [{"trigger_summary": "auth change -> docs updated", "similarity": 0.8}]

    async def procedures(q, limit=3):
        return [{"pattern_description": "inspect tokens first"}]

    node = MemoryGroundedNode(FakeInner(), None)
    grounded = await node._merge_sources(
        "task text", Bundle(knowledge, episodes, procedures)
    )
    assert "PKCE is required" in grounded
    assert "Similar past episode:" in grounded
    assert "Applicable procedure:" in grounded
    assert grounded.endswith("task text")


@pytest.mark.asyncio
async def test_missing_services_degrade_to_plain_task():
    inner = FakeInner()
    node = MemoryGroundedNode(inner, None)
    out = await node._ground("plain task")
    assert out == "plain task"


@pytest.mark.asyncio
async def test_source_failure_is_isolated():
    async def broken_knowledge(q, limit=5):
        raise RuntimeError("down")

    class Bundle2:
        knowledge = staticmethod(broken_knowledge)
        episodes = None
        procedures = None

    node = MemoryGroundedNode(FakeInner(), None)
    out = await node._merge_sources("task", Bundle2())
    assert out.endswith("task")
