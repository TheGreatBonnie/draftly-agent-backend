"""FakeTavilyClient is deterministic and performs no network.

Plan: docs/superpowers/plans/2026-09-20-tavily-rag.md (Task 4).
"""

from __future__ import annotations

import pytest

from draftly.integrations.tavily.errors import TavilyError, TavilyErrorCode
from tests.fakes import FakeTavilyClient


@pytest.mark.asyncio
async def test_no_network_and_deterministic() -> None:
    fake = FakeTavilyClient(
        map_urls=["https://docs.example.com/a", "https://docs.example.com/b"],
        crawl_pages={"https://docs.example.com/a": "# A"},
        extract_pages={"https://docs.example.com/b": "# B"},
        search_results=[
            {
                "title": "A",
                "url": "https://docs.example.com/a",
                "content": "alpha",
            }
        ],
        research_content={"facts": ["f1"]},
    )

    map_resp = await fake.map("https://docs.example.com")
    assert map_resp.urls == [
        "https://docs.example.com/a",
        "https://docs.example.com/b",
    ]

    crawl_resp = await fake.crawl("https://docs.example.com")
    assert crawl_resp.results[0].url == "https://docs.example.com/a"
    assert crawl_resp.results[0].content == "# A"

    extract_resp = await fake.extract(["https://docs.example.com/b"])
    assert extract_resp.results[0].raw_content == "# B"

    search_resp = await fake.search("alpha")
    assert search_resp.results[0].url == "https://docs.example.com/a"

    created = await fake.research(query="q")
    assert created.status == "pending"
    polled = await fake.research_poll(created.request_id, poll_timeout_seconds=5)
    assert polled.status == "completed"
    assert polled.content == {"facts": ["f1"]}

    # every method recorded its call
    assert [c[0] for c in fake.calls] == [
        "map",
        "crawl",
        "extract",
        "search",
        "research",
        "research_poll",
    ]


@pytest.mark.asyncio
async def test_scripted_failures_raise_taxonomy_codes() -> None:
    fake = FakeTavilyClient(fail_codes=["rate_limit", "credit_limit"])
    with pytest.raises(TavilyError) as first:
        await fake.search("q")
    assert first.value.code is TavilyErrorCode.RATE_LIMIT
    with pytest.raises(TavilyError) as second:
        await fake.map("https://docs.example.com")
    assert second.value.code is TavilyErrorCode.CREDIT_LIMIT
    # queue drained: next call succeeds
    resp = await fake.search("q")
    assert resp.results == []
