"""Behavior and controlled latency checks for onboarding knowledge extraction."""

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import draftly.memory.repository as memory_repository
import draftly.workflows.onboarding.stages as stages
from draftly.workflows.onboarding.stages import ExtractionOutput, run_knowledge_construction


def _context(chunks):
    context = MagicMock()
    context.memory.recall = AsyncMock(return_value=chunks)
    context.memory.store_batch = AsyncMock(return_value=[])
    context.docgraph.link = AsyncMock(return_value={"id": "edge"})
    context.candidates.enqueue = AsyncMock(return_value={"id": "candidate"})
    return context


def _result(output):
    return SimpleNamespace(
        structured_output=output,
        metrics=SimpleNamespace(accumulated_usage={"inputTokens": 100, "outputTokens": 20}),
    )


@pytest.mark.asyncio
async def test_twenty_pages_use_one_fresh_agent_per_group():
    """Catches per-chunk calls and pooled agents that retain prior chunk history."""
    chunks = [
        {
            "id": f"p{page}-c{part}",
            "content": f"Page {page} part {part}.",
            "metadata": {"document_id": f"page-{page}", "path": f"docs/{page}.md"},
        }
        for page in range(20)
        for part in range(4)
    ]
    context = _context(chunks)
    created = []

    class FakeAgent:
        def __init__(self, **kwargs):
            assert kwargs["system_prompt"] == stages.EXTRACTION_SYSTEM_PROMPT
            self.calls = 0
            created.append(self)

        async def invoke_async(self, prompt, *, structured_output_model, **kwargs):
            self.calls += 1
            assert self.calls == 1, "independent groups must not share agent history"
            ids = re.findall(r"Chunk ID: (\S+)", prompt)
            output = structured_output_model.model_validate(
                {"items": [{"chunk_id": cid, "facts": [f"fact for {cid}"]} for cid in ids]}
            )
            return _result(output)

    with patch("draftly.agents.factory.Agent", FakeAgent):
        result = await run_knowledge_construction(context, org_id="org", publish=AsyncMock())

    assert result.knowledge_count == 80
    assert result.failed_chunks == []
    assert len(created) == 20
    assert all(agent.calls == 1 for agent in created)


@pytest.mark.asyncio
async def test_fast_groups_publish_before_one_slow_group_finishes():
    """Catches a whole-batch barrier that hides completed, persisted work."""
    chunks = [
        {
            "id": f"p{page}-c{part}",
            "content": f"Page {page} part {part}.",
            "metadata": {"document_id": f"page-{page}"},
        }
        for page in range(10)
        for part in range(4)
    ]
    context = _context(chunks)
    slow_done = asyncio.Event()
    early_progress = False

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        async def invoke_async(self, prompt, *, structured_output_model, **kwargs):
            ids = re.findall(r"Chunk ID: (\S+)", prompt)
            if ids and ids[0].startswith("p0-"):
                await asyncio.sleep(0.2)
                slow_done.set()
            else:
                await asyncio.sleep(0.01)
            return _result(
                structured_output_model.model_validate(
                    {"items": [{"chunk_id": cid, "facts": [cid]} for cid in ids]}
                )
            )

    async def publish(kind, payload):
        nonlocal early_progress
        if kind == "tool_progress" and payload.get("processed", 0) > 0:
            early_progress |= not slow_done.is_set()

    with patch("draftly.agents.factory.Agent", FakeAgent):
        result = await run_knowledge_construction(context, org_id="org", publish=publish)

    assert result.knowledge_count == 40
    assert early_progress


@pytest.mark.asyncio
async def test_group_validation_retries_each_chunk_before_persisting():
    """A malformed group cannot lose or partially duplicate source facts."""
    chunks = [
        {"id": f"c{i}", "content": f"Distinct fact {i}.", "metadata": {"document_id": "same-page"}}
        for i in range(4)
    ]
    context = _context(chunks)
    prompts = []

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        async def invoke_async(self, prompt, *, structured_output_model, **kwargs):
            prompts.append(prompt)
            if "Chunk ID:" in prompt:
                return _result(
                    structured_output_model.model_validate(
                        {"items": [{"chunk_id": "c0", "facts": ["partial"]}]}
                    )
                )
            cid = re.search(r"Distinct fact (\d)", prompt).group(1)
            return _result(ExtractionOutput(facts=[f"fact-{cid}"]))

    with patch("draftly.agents.factory.Agent", FakeAgent):
        result = await run_knowledge_construction(context, org_id="org", publish=AsyncMock())

    assert len(prompts) == 5
    assert result.knowledge_count == 4
    assert result.failed_chunks == []
    stored = [
        item.content for call in context.memory.store_batch.await_args_list for item in call.args[0]
    ]
    assert sorted(stored) == ["fact-0", "fact-1", "fact-2", "fact-3"]


@pytest.mark.asyncio
async def test_grouping_respects_page_and_size_bounds():
    """A five-chunk page splits; oversized chunks never share a request."""
    chunks = [
        {"id": f"a{i}", "content": f"a{i} " + "A" * 3500, "metadata": {"document_id": "page-a"}}
        for i in range(3)
    ] + [
        {"id": f"b{i}", "content": f"b{i}", "metadata": {"document_id": "page-b"}} for i in range(5)
    ]
    context = _context(chunks)
    requests = []

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        async def invoke_async(self, prompt, *, structured_output_model, **kwargs):
            ids = re.findall(r"Chunk ID: (\S+)", prompt)
            requests.append(ids or [re.search(r"(a\d|b\d)", prompt).group(1)])
            if ids:
                return _result(
                    structured_output_model.model_validate(
                        {"items": [{"chunk_id": cid, "facts": [cid]} for cid in ids]}
                    )
                )
            return _result(ExtractionOutput(facts=[requests[-1][0]]))

    with patch("draftly.agents.factory.Agent", FakeAgent):
        result = await run_knowledge_construction(context, org_id="org", publish=AsyncMock())

    assert result.knowledge_count == 8
    assert sorted(map(tuple, requests)) == sorted(
        [
            ("a0",),
            ("a1",),
            ("a2",),
            ("b0", "b1", "b2", "b3"),
            ("b4",),
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("blank_content", ["", "   "])
async def test_empty_chunk_is_reported_without_an_llm_call(blank_content):
    """Empty content cannot produce a fabricated fact or spend a request."""
    chunks = [
        {"id": "empty", "content": blank_content, "metadata": {"document_id": "page"}},
        {"id": "valid", "content": "Install the package.", "metadata": {"document_id": "page"}},
    ]
    context = _context(chunks)
    prompts = []

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        async def invoke_async(self, prompt, **kwargs):
            prompts.append(prompt)
            ids = re.findall(r"Chunk ID: (\S+)", prompt)
            if ids:
                return _result(
                    kwargs["structured_output_model"].model_validate(
                        {"items": [{"chunk_id": cid, "facts": [cid]} for cid in ids]}
                    )
                )
            return _result(ExtractionOutput(facts=["install fact"]))

    with patch("draftly.agents.factory.Agent", FakeAgent):
        result = await run_knowledge_construction(context, org_id="org", publish=AsyncMock())

    assert result.knowledge_count == 1
    assert result.failed_chunks == ["empty"]
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_llm_completion_log_contains_latency_and_input_tokens():
    """Operators can distinguish slow model calls from input growth."""

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        async def invoke_async(self, prompt, **kwargs):
            return _result(ExtractionOutput(facts=["fact"]))

    with (
        patch("draftly.agents.factory.Agent", FakeAgent),
        patch.object(stages.logger, "info") as info,
    ):
        await stages._llm_generate(MagicMock(), "prompt", output_model=ExtractionOutput)

    completion = [call for call in info.call_args_list if call.args[0] == "llm_generate_done"]
    assert len(completion) == 1
    assert completion[0].kwargs["input_tokens"] == 100
    assert completion[0].kwargs["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_memory_store_log_separates_embedding_and_database_time():
    """Operators can identify which half of fact persistence is slow."""
    embeddings = MagicMock()
    embeddings.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])
    store = MagicMock()
    store.create_batch = AsyncMock(return_value=[{"id": "fact"}])
    repo = memory_repository.DomainMemoryRepository(repository=store, embeddings=embeddings)
    item = SimpleNamespace(
        namespace="knowledge",
        content="fact",
        memory_type="fact",
        importance=0.5,
        confidence=0.8,
        metadata={},
        org_id="org",
    )

    with patch.object(memory_repository.logger, "info") as info:
        await repo.store_batch([item])

    timing = [call for call in info.call_args_list if call.args[0] == "memory_store_batch_timing"]
    assert len(timing) == 1
    assert timing[0].kwargs["count"] == 1
    assert timing[0].kwargs["embedding_ms"] >= 0
    assert timing[0].kwargs["database_ms"] >= 0
