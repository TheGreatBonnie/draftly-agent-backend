"""MemoryGroundedNode: organizational memory grounds the context node."""

from __future__ import annotations

from typing import Any

from strands.multiagent.base import MultiAgentBase, MultiAgentResult, Status

from draftly.agents.shared.memory_grounding import MemoryGroundedNode


class RecordingInner(MultiAgentBase):
    name = "context"

    def __init__(self) -> None:
        super().__init__()
        self.received: Any = None

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        self.received = task
        return MultiAgentResult(status=Status.COMPLETED)


class FakeMemory:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.queries: list[str] = []

    async def recall_knowledge(self, query: str, limit: int = 5):
        self.queries.append(query)
        return self.items[:limit]


class TestMemoryGroundedNode:
    async def test_prepends_knowledge_to_task(self) -> None:
        inner = RecordingInner()
        memory = FakeMemory([{"content": "API keys rotate via vault"}])
        node = MemoryGroundedNode(inner, memory)

        await node.invoke_async("How do I rotate API keys?")

        assert isinstance(inner.received, str)
        assert inner.received.startswith("Relevant organizational knowledge:")
        assert "vault" in inner.received
        assert inner.received.endswith("How do I rotate API keys?")
        assert memory.queries == ["How do I rotate API keys?"]

    async def test_passthrough_without_memory(self) -> None:
        inner = RecordingInner()
        node = MemoryGroundedNode(inner, None)

        await node.invoke_async("plain task")

        assert inner.received == "plain task"

    async def test_memory_failure_degrades_silently(self) -> None:
        class BrokenMemory:
            async def recall_knowledge(self, query: str, limit: int = 5):
                raise RuntimeError("db down")

        inner = RecordingInner()
        node = MemoryGroundedNode(inner, BrokenMemory())

        await node.invoke_async("task")

        assert inner.received == "task"

    async def test_no_matches_leaves_task_untouched(self) -> None:
        inner = RecordingInner()
        memory = FakeMemory([])
        node = MemoryGroundedNode(inner, memory)

        await node.invoke_async("task")

        assert inner.received == "task"
