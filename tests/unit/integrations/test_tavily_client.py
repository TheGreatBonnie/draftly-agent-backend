"""Typed Tavily client: request construction, response parsing, error mapping.

No live network: every test injects httpx.MockTransport (or a small in-test
async transport). Spec: docs/superpowers/specs/2026-09-20-tavily-rag-design.md.
Plan: docs/superpowers/plans/2026-09-20-tavily-rag.md (Task 3).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from draftly.integrations.tavily.client import TavilyClient
from draftly.integrations.tavily.errors import TavilyError, TavilyErrorCode

API_KEY = "tvly-test-key"


def _search_payload() -> dict[str, Any]:
    return {
        "query": "docs query",
        "results": [
            {
                "title": "Getting started",
                "url": "https://docs.example.com/start",
                "content": "Install then run.",
                "score": 0.91,
            }
        ],
        "request_id": "req-search-1",
        "usage": {"credits_used": 2.0},
    }


def _client(handler, **kwargs) -> TavilyClient:
    return TavilyClient(
        API_KEY, transport=httpx.MockTransport(handler), **kwargs
    )


@pytest.mark.asyncio
async def test_search_builds_request_and_parses() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_search_payload())

    client = _client(handler)
    try:
        resp = await client.search(
            "docs query",
            search_depth="advanced",
            max_results=5,
            chunks_per_source=3,
            include_domains=["docs.example.com"],
        )
    finally:
        await client.aclose()

    assert seen["method"] == "POST"
    assert seen["url"].endswith("/search")
    assert seen["auth"] == f"Bearer {API_KEY}"
    assert seen["body"]["query"] == "docs query"
    assert seen["body"]["search_depth"] == "advanced"
    assert seen["body"]["max_results"] == 5
    assert seen["body"]["include_domains"] == ["docs.example.com"]
    assert seen["body"]["include_domains_mode"] == "restrict"
    assert resp.results[0].url == "https://docs.example.com/start"
    assert resp.results[0].score == 0.91
    assert resp.request_id == "req-search-1"


@pytest.mark.asyncio
async def test_extract_chunks_to_20_and_collects_failed_results() -> None:
    calls: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        urls = body["urls"]
        calls.append(urls)
        assert len(urls) <= 20
        return httpx.Response(
            200,
            json={
                "results": [
                    {"url": u, "raw_content": f"# {u}"} for u in urls[:-1]
                ],
                "failed_results": [
                    {"url": urls[-1], "error": "unsupported content type"}
                ],
                "request_id": "req-extract-1",
            },
        )

    urls = [f"https://docs.example.com/p{i}" for i in range(25)]
    client = _client(handler)
    try:
        resp = await client.extract(urls)
    finally:
        await client.aclose()

    assert [len(c) for c in calls] == [20, 5]
    assert len(resp.results) == 23
    assert resp.results[0].raw_content.startswith("# https://")
    # failed_results accumulate across batches
    assert {f.url for f in resp.failed_results} == {
        "https://docs.example.com/p19",
        "https://docs.example.com/p24",
    }
    assert resp.failed_results[0].error == "unsupported content type"


@pytest.mark.asyncio
async def test_research_create_returns_request_id() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(201, json={"request_id": "req-9", "status": "pending"})

    client = _client(handler)
    try:
        resp = await client.research(
            query="summarize",
            model="mini",
            files=[{"filename": "a.md", "content_b64": "IyBoaQ=="}],
            output_schema={"type": "object", "properties": {"a": {"type": "string"}}},
        )
    finally:
        await client.aclose()

    assert seen["url"].endswith("/research")
    assert seen["body"]["model"] == "mini"
    assert seen["body"]["files"][0]["filename"] == "a.md"
    assert resp.request_id == "req-9"
    assert resp.status == "pending"


@pytest.mark.asyncio
async def test_research_poll_pending_to_completed() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert str(request.url).endswith("/research/req-9")
        if calls["n"] < 3:
            return httpx.Response(202, json={"request_id": "req-9", "status": "pending"})
        return httpx.Response(
            200,
            json={
                "request_id": "req-9",
                "status": "completed",
                "content": {"a": "done"},
                "sources": [{"url": "https://docs.example.com/start"}],
            },
        )

    client = _client(handler)
    try:
        resp = await client.research_poll("req-9", poll_timeout_seconds=5)
    finally:
        await client.aclose()

    assert resp.status == "completed"
    assert resp.content == {"a": "done"}
    assert resp.sources == [{"url": "https://docs.example.com/start"}]
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_research_poll_times_out_raises_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"request_id": "req-9", "status": "pending"})

    client = _client(handler)
    try:
        with pytest.raises(TavilyError) as exc_info:
            await client.research_poll(
                "req-9", poll_timeout_seconds=0.05, poll_interval_seconds=0.01
            )
    finally:
        await client.aclose()

    assert exc_info.value.code is TavilyErrorCode.TIMEOUT


@pytest.mark.asyncio
async def test_research_poll_failed_status_raises_upstream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"request_id": "req-9", "status": "failed", "content": None}
        )

    client = _client(handler)
    try:
        with pytest.raises(TavilyError) as exc_info:
            await client.research_poll("req-9", poll_timeout_seconds=5)
    finally:
        await client.aclose()

    assert exc_info.value.code is TavilyErrorCode.UPSTREAM


@pytest.mark.asyncio
async def test_http_401_maps_to_authentication() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid api key"})

    client = _client(handler)
    try:
        with pytest.raises(TavilyError) as exc_info:
            await client.search("q")
    finally:
        await client.aclose()

    assert exc_info.value.code is TavilyErrorCode.AUTHENTICATION


@pytest.mark.asyncio
async def test_http_429_maps_to_rate_limit_and_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, headers={"Retry-After": "7"}, json={"detail": "slow down"}
        )

    client = _client(handler)
    try:
        with pytest.raises(TavilyError) as exc_info:
            await client.search("q")
    finally:
        await client.aclose()

    assert exc_info.value.code is TavilyErrorCode.RATE_LIMIT
    assert exc_info.value.retry_after_seconds == 7.0


@pytest.mark.asyncio
async def test_http_402_maps_to_credit_limit_not_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"detail": "usage limit exceeded"})

    client = _client(handler)
    try:
        with pytest.raises(TavilyError) as exc_info:
            await client.search("q")
    finally:
        await client.aclose()

    from draftly.integrations.tavily.errors import is_retryable

    assert exc_info.value.code is TavilyErrorCode.CREDIT_LIMIT
    assert is_retryable(exc_info.value.code) is False


@pytest.mark.asyncio
async def test_http_500_maps_to_upstream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    client = _client(handler)
    try:
        with pytest.raises(TavilyError) as exc_info:
            await client.search("q")
    finally:
        await client.aclose()

    assert exc_info.value.code is TavilyErrorCode.UPSTREAM


@pytest.mark.asyncio
async def test_invalid_payload_maps_to_invalid_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"nonsense": True})

    client = _client(handler)
    try:
        with pytest.raises(TavilyError) as exc_info:
            await client.search("q")
    finally:
        await client.aclose()

    assert exc_info.value.code is TavilyErrorCode.INVALID_RESPONSE


@pytest.mark.asyncio
async def test_transport_timeout_maps_to_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out")

    client = _client(handler)
    try:
        with pytest.raises(TavilyError) as exc_info:
            await client.search("q")
    finally:
        await client.aclose()

    assert exc_info.value.code is TavilyErrorCode.TIMEOUT


class _TrackingTransport(httpx.AsyncBaseTransport):
    """Real async transport that records peak in-flight requests."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.02)
            return httpx.Response(200, json=_search_payload())
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_max_concurrency_bounded() -> None:
    transport = _TrackingTransport()
    client = TavilyClient(API_KEY, max_concurrency=3, transport=transport)
    try:
        await asyncio.gather(*[client.search(f"q{i}") for i in range(9)])
    finally:
        await client.aclose()

    assert transport.peak <= 3
    assert transport.peak >= 2


@pytest.mark.asyncio
async def test_logs_redact_content_and_keys() -> None:
    from structlog.testing import capture_logs

    secret_query = "distinctive-query-zxqw-4829"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_search_payload())

    client = _client(handler)
    try:
        with capture_logs() as logs:
            await client.search(secret_query)
    finally:
        await client.aclose()

    flat = json.dumps(logs, default=str)
    assert API_KEY not in flat
    assert secret_query not in flat
    # telemetry still present
    assert "req-search-1" in flat


@pytest.mark.asyncio
async def test_crawl_builds_request_and_parses() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "results": [
                    {"url": "https://docs.example.com/a", "raw_content": "# A"}
                ],
                "request_id": "req-crawl-1",
            },
        )

    client = _client(handler)
    try:
        resp = await client.crawl("https://docs.example.com", max_depth=2, limit=10)
    finally:
        await client.aclose()

    assert seen["url"].endswith("/crawl")
    assert seen["body"]["extract_depth"] == "advanced"
    assert seen["body"]["format"] == "markdown"
    assert resp.results[0].url == "https://docs.example.com/a"
    assert resp.results[0].content == "# A"
    assert resp.request_id == "req-crawl-1"


@pytest.mark.asyncio
async def test_map_builds_request_and_parses() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "urls": [
                    "https://docs.example.com/start",
                    "https://docs.example.com/start#intro",
                    "https://other.example.com/x",
                ],
                "limit": 50,
            },
        )

    client = _client(handler)
    try:
        resp = await client.map(
            "https://docs.example.com",
            max_depth=3,
            select_paths=["/docs/.*"],
            allow_external=False,
        )
    finally:
        await client.aclose()

    assert seen["url"].endswith("/map")
    assert seen["body"]["allow_external"] is False
    assert resp.urls == [
        "https://docs.example.com/start",
        "https://docs.example.com/start#intro",
        "https://other.example.com/x",
    ]
