"""Stage 2 retrieval-guided synthesis behind TAVILY_RESEARCH_ENABLED.

Public sources only: chunk content sent to Tavily Research must already be
public. GitHub sources always keep the per-chunk LLM loop.

Plan: plans/2026-09-20-tavily-rag.md (Task 11).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from draftly.integrations.tavily.errors import TavilyError, TavilyErrorCode
from draftly.workflows.onboarding.stages import (
    ExtractionOutput,
    run_knowledge_construction,
)
from tests.fakes import FakeTavilyClient

ROOT = "https://docs.example.com"


def _chunk(cid: str, url: str, content: str, page_type: str = "reference") -> dict:
    return {
        "id": cid,
        "content": content,
        "start_line": 1,
        "metadata": {
            "document_id": f"doc-{url}",
            "source_url": url,
            "source_id": url,
            "page_type": page_type,
        },
    }


def _context(chunks: list, *, flag_on: bool = True, budget=None):
    context = MagicMock()
    context.model = None
    context.memory.recall = AsyncMock(return_value=chunks)
    context.memory.store_batch = AsyncMock(return_value=[])
    context.docgraph.link = AsyncMock(return_value={})
    context.candidates.enqueue = AsyncMock(return_value={})
    context.config = SimpleNamespace(
        tavily_api_key="tvly-test-key" if flag_on else None,
        tavily_base_url="https://api.tavily.com",
        tavily_request_timeout_seconds=60,
        tavily_research_poll_timeout_seconds=300,
        tavily_max_concurrency=4,
        tavily_credit_budget=budget,
        tavily_research_enabled=flag_on,
    )
    return context


def _extraction_content() -> dict:
    return {
        "facts": ["fact one", "fact one", "fact two"],
        "relationships": [
            {"source": "a", "target": "b", "type": "IMPLEMENTS"},
            {"source": "x", "target": "y", "type": "BOGUS"},
        ],
        "procedures": [{"title": "Deploy", "steps": ["run it"]}],
    }


def _pages(n: int) -> list:
    return [
        _chunk(f"c{i}", f"{ROOT}/p{i}", f"# P{i}\n\nBody of page {i}.")
        for i in range(n)
    ]


def _run(context, source_type: str = "public_documentation", publish=None):
    publish = publish if publish is not None else AsyncMock()
    return run_knowledge_construction(
        context,
        org_id="o",
        publish=publish,
        source_type=source_type,
    )


@pytest.mark.asyncio
async def test_synthesis_uses_research_shards_not_per_page_loop() -> None:
    fake = FakeTavilyClient(research_content=_extraction_content())
    context = _context(_pages(6))
    publish = AsyncMock()
    with patch(
        "draftly.integrations.tavily.client.TavilyClient", return_value=fake
    ):
        result = await _run(context, publish=publish)

    research_calls = [c for c in fake.calls if c[0] == "research"]
    assert len(research_calls) == 2  # 6 pages -> shards of 5 + 1
    for _, kwargs in research_calls:
        assert kwargs["model"] == "mini"
        assert len(kwargs["files"]) <= 5
        assert "properties" in kwargs["output_schema"]
    assert result.knowledge_count == 2 + 2  # deduped facts per shard x2 shards
    progress = [
        c for c in publish.await_args_list if c.args[0] == "tool_progress"
    ]
    assert progress
    assert progress[0].args[1]["name"] == "knowledge_extraction"


@pytest.mark.asyncio
async def test_shard_failure_records_failed_sources() -> None:
    fake = FakeTavilyClient(research_content="not-a-dict")
    context = _context(_pages(2))
    with patch(
        "draftly.integrations.tavily.client.TavilyClient", return_value=fake
    ):
        result = await _run(context)

    assert sorted(result.failed_chunks) == [f"{ROOT}/p0", f"{ROOT}/p1"]
    assert result.knowledge_count == 0


@pytest.mark.asyncio
async def test_credit_limit_hard_fails_stage() -> None:
    fake = FakeTavilyClient(fail_codes=["credit_limit"])
    context = _context(_pages(1))
    with (
        patch(
            "draftly.integrations.tavily.client.TavilyClient", return_value=fake
        ),
        pytest.raises(TavilyError) as exc_info,
    ):
        await _run(context)

    assert exc_info.value.code is TavilyErrorCode.CREDIT_LIMIT


@pytest.mark.asyncio
async def test_retryable_error_halts_stage() -> None:
    fake = FakeTavilyClient(fail_codes=["upstream"])
    context = _context(_pages(1))
    with (
        patch(
            "draftly.integrations.tavily.client.TavilyClient", return_value=fake
        ),
        pytest.raises(TavilyError) as exc_info,
    ):
        await _run(context)

    assert exc_info.value.code is TavilyErrorCode.UPSTREAM


@pytest.mark.asyncio
async def test_relation_whitelist_and_dedup() -> None:
    fake = FakeTavilyClient(research_content=_extraction_content())
    context = _context(_pages(1))
    with patch(
        "draftly.integrations.tavily.client.TavilyClient", return_value=fake
    ):
        await _run(context)

    stored = context.memory.store_batch.await_args.args[0]
    assert sorted(f.content for f in stored) == ["fact one", "fact two"]
    link_types = [
        c.kwargs["relation_type"] for c in context.docgraph.link.await_args_list
    ]
    assert "BOGUS" not in link_types
    assert "DERIVED_FROM" in link_types  # whitelist fallback
    assert "IMPLEMENTS" in link_types
    # deduped: one candidates.enqueue for the single procedure
    assert context.candidates.enqueue.await_count == 1


@pytest.mark.asyncio
async def test_flag_off_keeps_existing_loop() -> None:
    context = _context(_pages(1), flag_on=False)
    with (
        patch(
            "draftly.integrations.tavily.client.TavilyClient"
        ) as client_cls,
        patch(
            "draftly.workflows.onboarding.stages._agent_pool", return_value=None
        ),
        patch(
            "draftly.workflows.onboarding.stages._llm_generate",
            new=AsyncMock(return_value=ExtractionOutput(facts=["legacy fact"])),
        ),
    ):
        result = await _run(context)

    client_cls.assert_not_called()
    assert result.knowledge_count == 1


@pytest.mark.asyncio
async def test_github_source_never_uses_research() -> None:
    context = _context(_pages(1), flag_on=True)
    with (
        patch(
            "draftly.integrations.tavily.client.TavilyClient"
        ) as client_cls,
        patch(
            "draftly.workflows.onboarding.stages._agent_pool", return_value=None
        ),
        patch(
            "draftly.workflows.onboarding.stages._llm_generate",
            new=AsyncMock(return_value=ExtractionOutput(facts=["legacy fact"])),
        ),
    ):
        result = await _run(context, source_type="github_repository")

    # Private content must never reach Tavily, even with the flag on.
    client_cls.assert_not_called()
    assert result.knowledge_count == 1
