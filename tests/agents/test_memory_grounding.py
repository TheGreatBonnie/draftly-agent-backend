"""MemoryGroundedNode: organizational memory grounds the context node."""

from __future__ import annotations

from typing import Any

from strands.agent.agent_result import AgentResult
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, Status
from strands.telemetry.metrics import EventLoopMetrics
from strands.types.content import ContentBlock, Message

from draftly.agents.schemas import EvidenceBundle
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


class StructuredResultInner(MultiAgentBase):
    """Inner that returns an AgentResult carrying structured output — exactly
    what the real context agent (structured_output_model=EvidenceBundle)
    produces. The wrapper must pass it through untouched."""

    name = "context"

    def __init__(self) -> None:
        super().__init__()
        self.received: Any = None
        self.value: AgentResult | None = None

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        del invocation_state, kwargs
        self.received = task
        self.value = AgentResult(
            stop_reason="end_turn",
            message=Message(
                content=[ContentBlock(text="Evidence bundle compiled via tool call.")],
                role="assistant",
            ),
            metrics=EventLoopMetrics(),
            state=None,
            structured_output=EvidenceBundle(
                items=[{"id": "docs/auth.md", "topic": "authentication"}],
                summary="auth evidence",
            ),
        )
        return self.value


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

    async def test_retrieval_is_scoped_to_workflow_organization(self) -> None:
        inner = RecordingInner()
        memory = FakeMemory([])
        memory.org_ids: list[str | None] = []

        async def recall_knowledge(query: str, limit: int = 5, org_id: str | None = None):
            memory.queries.append(query)
            memory.org_ids.append(org_id)
            return memory.items[:limit]

        memory.recall_knowledge = recall_knowledge
        node = MemoryGroundedNode(inner, memory)

        await node.invoke_async("PR task", invocation_state={"project_id": "org-7"})

        assert memory.org_ids == ["org-7"]

    async def test_bundle_grounding_includes_episodes_and_procedures(self) -> None:
        inner = RecordingInner()
        calls: list[tuple[str, str | None]] = []

        class Bundle:
            async def knowledge(self, query: str, *, org_id: str | None = None):
                calls.append(("knowledge", org_id))
                return [{"content": "known fact"}]

            async def episodes(self, query: str, *, limit: int = 2, org_id: str | None = None):
                calls.append(("episodes", org_id))
                return [{"trigger_summary": "similar PR"}]

            async def procedures(self, query: str, *, limit: int = 1, org_id: str | None = None):
                calls.append(("procedures", org_id))
                return [{"pattern_description": "update docs then review"}]

        node = MemoryGroundedNode(inner, Bundle())
        await node.invoke_async("PR task", invocation_state={"project_id": "org-7"})

        assert "known fact" in inner.received
        assert "similar PR" in inner.received
        assert "update docs then review" in inner.received
        assert calls == [("knowledge", "org-7"), ("episodes", "org-7"), ("procedures", "org-7")]

    async def test_wrapper_preserves_inner_structured_agent_result(self) -> None:
        """The wrapper must NOT strip the inner agent's structured output.

        Regression: a memory-grounded context returned an empty
        MultiAgentResult, so the EvidenceBundle never reached the evaluator
        and runs looped in revision until the execution timeout killed them.
        """
        inner = StructuredResultInner()
        node = MemoryGroundedNode(inner, None)

        result = await node.invoke_async("task")

        preserved = result.results["context"].result
        assert isinstance(preserved, AgentResult)
        assert result.results["context"].status == Status.COMPLETED
        assert preserved is inner.value
        assert preserved.structured_output is not None
        assert inner.value is not None
        assert inner.value.structured_output is not None
        assert "docs/auth.md" in inner.value.structured_output.model_dump_json()

    async def test_wrapper_preserves_structured_result_when_grounded(self) -> None:
        inner = StructuredResultInner()
        memory = FakeMemory([{"content": "API keys rotate via vault"}])
        node = MemoryGroundedNode(inner, memory)

        result = await node.invoke_async("How do I rotate API keys?")

        preserved = result.results["context"].result
        assert isinstance(preserved, AgentResult)
        assert preserved.structured_output is not None
        assert inner.received.startswith("Relevant organizational knowledge:")
        assert "API keys rotate via vault" in inner.received

    async def test_structured_preserve_is_logged(self, monkeypatch) -> None:
        import structlog
        from structlog.testing import capture_logs

        import draftly.agents.shared.memory_grounding as grounding_module

        node = MemoryGroundedNode(StructuredResultInner(), None)

        with capture_logs() as logs:
            monkeypatch.setattr(
                grounding_module,
                "logger",
                structlog.get_logger("test.memory_grounding.evidence"),
            )
            await node.invoke_async("task")

        markers = [
            line for line in logs if line.get("event") == "memory_grounded_structured_preserved"
        ]
        assert len(markers) == 1
        assert markers[0]["agent"] == "context"
        assert markers[0]["structured"] is True
        assert markers[0]["evidence_items"] == 1

    async def test_plain_text_result_warns_missing_structured_output(self, monkeypatch) -> None:
        import structlog
        from structlog.testing import capture_logs

        import draftly.agents.shared.memory_grounding as grounding_module

        class PlainResultInner(MultiAgentBase):
            name = "context"

            async def invoke_async(
                self,
                task: Any,
                invocation_state: dict[str, Any] | None = None,
                **kwargs: Any,
            ) -> AgentResult:
                del task, invocation_state, kwargs
                return AgentResult(
                    stop_reason="end_turn",
                    message=Message(content=[ContentBlock(text="plain text")], role="assistant"),
                    metrics=EventLoopMetrics(),
                    state=None,
                    structured_output=None,
                )

        node = MemoryGroundedNode(PlainResultInner(), None)

        with capture_logs() as logs:
            monkeypatch.setattr(
                grounding_module,
                "logger",
                structlog.get_logger("test.memory_grounding.plain"),
            )
            await node.invoke_async("task")

        warnings = [
            line for line in logs if line.get("event") == "memory_grounded_no_structured_output"
        ]
        assert len(warnings) == 1
        assert warnings[0]["agent"] == "context"


class EmptyThenFilledInner(MultiAgentBase):
    """Inner that returns an empty EvidenceBundle once, then a filled one —
    the parse-drop signature (items==0) that must trigger a single re-emit."""

    name = "context"

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Any] = []

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        del invocation_state, kwargs
        self.calls.append(task)
        structured = (
            EvidenceBundle(
                items=[{"id": "docs/auth.md", "topic": "authentication"}],
                summary="retrieved",
            )
            if len(self.calls) > 1
            else EvidenceBundle(items=[], summary="")
        )
        return AgentResult(
            stop_reason="end_turn",
            message=Message(content=[ContentBlock(text="evidence")], role="assistant"),
            metrics=EventLoopMetrics(),
            state=None,
            structured_output=structured,
        )


class AlwaysEmptyInner(MultiAgentBase):
    """Inner that always returns an empty EvidenceBundle (items==0)."""

    name = "context"

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        del task, invocation_state, kwargs
        self.calls += 1
        return AgentResult(
            stop_reason="end_turn",
            message=Message(content=[ContentBlock(text="no evidence")], role="assistant"),
            metrics=EventLoopMetrics(),
            state=None,
            structured_output=EvidenceBundle(items=[], summary=""),
        )


class TestEmptyEvidenceRetry:
    async def test_empty_bundle_triggers_single_reeemit(self) -> None:
        inner = EmptyThenFilledInner()
        node = MemoryGroundedNode(inner, None)

        result = await node.invoke_async("collect evidence")

        assert len(inner.calls) == 2
        assert "EvidenceBundle" in inner.calls[1]
        preserved = result.results["context"].result
        assert isinstance(preserved, AgentResult)
        assert preserved.structured_output is not None
        assert len(preserved.structured_output.items) == 1

    async def test_persistently_empty_bundle_is_retried_once_then_degraded(
        self, monkeypatch
    ) -> None:
        import structlog
        from structlog.testing import capture_logs

        import draftly.agents.shared.memory_grounding as grounding_module

        inner = AlwaysEmptyInner()
        node = MemoryGroundedNode(inner, None)

        with capture_logs() as logs:
            monkeypatch.setattr(
                grounding_module,
                "logger",
                structlog.get_logger("test.memory_grounding.empty"),
            )
            result = await node.invoke_async("collect evidence")

        assert inner.calls == 2
        preserved = result.results["context"].result
        assert preserved.structured_output is not None
        assert len(preserved.structured_output.items) == 0
        degraded = [
            line for line in logs if line.get("event") == "memory_grounded_empty_evidence_degraded"
        ]
        assert len(degraded) == 1
        assert degraded[0]["agent"] == "context"

    async def test_non_empty_bundle_is_not_retried(self) -> None:
        class CountingBundleInner(MultiAgentBase):
            name = "context"

            def __init__(self) -> None:
                super().__init__()
                self.calls = 0

            async def invoke_async(
                self,
                task: Any,
                invocation_state: dict[str, Any] | None = None,
                **kwargs: Any,
            ) -> AgentResult:
                del task, invocation_state, kwargs
                self.calls += 1
                return AgentResult(
                    stop_reason="end_turn",
                    message=Message(content=[ContentBlock(text="evidence")], role="assistant"),
                    metrics=EventLoopMetrics(),
                    state=None,
                    structured_output=EvidenceBundle(
                        items=[{"id": "docs/auth.md", "topic": "authentication"}],
                        summary="auth evidence",
                    ),
                )

        inner = CountingBundleInner()
        node = MemoryGroundedNode(inner, None)

        result = await node.invoke_async("task")

        assert inner.calls == 1
        preserved = result.results["context"].result
        assert preserved.structured_output is not None
        assert len(preserved.structured_output.items) == 1
