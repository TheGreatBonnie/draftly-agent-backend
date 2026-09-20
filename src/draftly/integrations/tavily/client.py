"""Async typed Tavily client with injectable transport.

Tavily is the ingestion/freshness layer only — never the permanent vector DB.
Unit tests inject ``httpx.MockTransport`` (or any ``AsyncBaseTransport``); no
live network in default tests.

Structured logs carry endpoint/latency/status/request_id/credits only — never
page content, attached files, API keys, or raw responses.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
import structlog
from pydantic import ValidationError

from draftly.integrations.tavily.errors import TavilyError, TavilyErrorCode
from draftly.integrations.tavily.models import (
    CrawlResponse,
    ExtractResponse,
    MapResponse,
    ResearchPollResponse,
    ResearchResponse,
    SearchResponse,
)

logger = structlog.get_logger("draftly.integrations.tavily")

_EXTRACT_BATCH_SIZE = 20
_UNSUPPORTED_RE = re.compile(r"unsupport", re.IGNORECASE)


class TavilyClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.tavily.com",
        timeout_seconds: int = 60,
        max_concurrency: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_concurrency = max_concurrency
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                transport=self._transport,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> TavilyClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        search_depth: str = "advanced",
        max_results: int = 8,
        chunks_per_source: int = 3,
        include_domains: Sequence[str] = (),
        include_domains_mode: str = "restrict",
        include_answer: bool = False,
        include_raw_content: bool = False,
    ) -> SearchResponse:
        async with self._semaphore:
            payload = {
                "query": query,
                "search_depth": search_depth,
                "max_results": max_results,
                "chunks_per_source": chunks_per_source,
                "include_domains": list(include_domains),
                "include_domains_mode": include_domains_mode,
                "include_answer": include_answer,
                "include_raw_content": include_raw_content,
            }
            body = await self._post("/search", payload)
            return self._parse(SearchResponse, body, "/search")

    async def map(
        self,
        url: str,
        *,
        max_depth: int = 3,
        max_breadth: int = 20,
        limit: int = 50,
        select_paths: Sequence[str] = (),
        select_domains: Sequence[str] = (),
        exclude_paths: Sequence[str] = (),
        exclude_domains: Sequence[str] = (),
        categories: Sequence[str] = (),
        allow_external: bool = False,
    ) -> MapResponse:
        async with self._semaphore:
            payload = {
                "url": url,
                "max_depth": max_depth,
                "max_breadth": max_breadth,
                "limit": limit,
                "select_paths": list(select_paths),
                "select_domains": list(select_domains),
                "exclude_paths": list(exclude_paths),
                "exclude_domains": list(exclude_domains),
                "categories": list(categories),
                "allow_external": allow_external,
            }
            body = await self._post("/map", payload)
            return self._parse(MapResponse, body, "/map")

    async def crawl(
        self,
        url: str,
        *,
        max_depth: int = 3,
        max_breadth: int = 20,
        limit: int = 50,
        select_paths: Sequence[str] = (),
        exclude_paths: Sequence[str] = (),
        exclude_domains: Sequence[str] = (),
        extract_depth: str = "advanced",
        format: str = "markdown",  # noqa: A002 - Tavily API field name
        include_images: bool = False,
    ) -> CrawlResponse:
        async with self._semaphore:
            payload = {
                "url": url,
                "max_depth": max_depth,
                "max_breadth": max_breadth,
                "limit": limit,
                "select_paths": list(select_paths),
                "exclude_paths": list(exclude_paths),
                "exclude_domains": list(exclude_domains),
                "extract_depth": extract_depth,
                "format": format,
                "include_images": include_images,
            }
            body = await self._post("/crawl", payload)
            return self._parse(CrawlResponse, body, "/crawl")

    async def extract(
        self,
        urls: Sequence[str],
        *,
        extract_depth: str = "advanced",
        format: str = "markdown",  # noqa: A002 - Tavily API field name
    ) -> ExtractResponse:
        async with self._semaphore:
            merged_results: list[dict[str, Any]] = []
            merged_failed: list[dict[str, Any]] = []
            usage: dict[str, Any] | None = None
            request_id: str | None = None
            for i in range(0, len(urls), _EXTRACT_BATCH_SIZE):
                batch = list(urls[i : i + _EXTRACT_BATCH_SIZE])
                body = await self._post(
                    "/extract",
                    {"urls": batch, "extract_depth": extract_depth, "format": format},
                )
                merged_results.extend(body.get("results", []))
                merged_failed.extend(body.get("failed_results", []))
                usage = body.get("usage", usage)
                request_id = body.get("request_id", request_id)
            return self._parse(
                ExtractResponse,
                {
                    "results": merged_results,
                    "failed_results": merged_failed,
                    "usage": usage,
                    "request_id": request_id,
                },
                "/extract",
            )

    async def research(
        self,
        *,
        query: str,
        model: str = "mini",
        files: Sequence[Mapping[str, Any]] = (),
        output_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        include_domains: Sequence[str] = (),
        citation_format: str = "markdown",
    ) -> ResearchResponse:
        async with self._semaphore:
            payload: dict[str, Any] = {
                "query": query,
                "model": model,
                "files": [dict(f) for f in files],
                "include_domains": list(include_domains),
                "citation_format": citation_format,
            }
            if output_schema is not None:
                payload["output_schema"] = output_schema
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            body = await self._post("/research", payload, expected_status=201)
            return self._parse(ResearchResponse, body, "/research")

    async def research_poll(
        self,
        request_id: str,
        *,
        poll_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 2.0,
    ) -> ResearchPollResponse:
        deadline = time.monotonic() + poll_timeout_seconds
        async with self._semaphore:
            while True:
                body = await self._get(f"/research/{request_id}")
                status = str(body.get("status", "")).lower()
                if status == "completed":
                    return self._parse(
                        ResearchPollResponse, body, f"/research/{request_id}"
                    )
                if status == "failed":
                    raise TavilyError(
                        TavilyErrorCode.UPSTREAM,
                        f"research {request_id} failed",
                        request_id=request_id,
                    )
                if time.monotonic() >= deadline:
                    raise TavilyError(
                        TavilyErrorCode.TIMEOUT,
                        f"research {request_id} poll timed out",
                        request_id=request_id,
                    )
                await asyncio.sleep(poll_interval_seconds)

    # ------------------------------------------------------------------
    # Transport helpers
    # ------------------------------------------------------------------

    async def _post(
        self, path: str, payload: dict[str, Any], *, expected_status: int = 200
    ) -> dict[str, Any]:
        start = time.monotonic()
        try:
            response = await self._http().post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise TavilyError(
                TavilyErrorCode.TIMEOUT, f"tavily {path} timed out: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TavilyError(
                TavilyErrorCode.UPSTREAM, f"tavily {path} transport error: {exc}"
            ) from exc
        return self._handle(path, response, expected_status, start)

    async def _get(self, path: str) -> dict[str, Any]:
        start = time.monotonic()
        try:
            response = await self._http().get(path)
        except httpx.TimeoutException as exc:
            raise TavilyError(
                TavilyErrorCode.TIMEOUT, f"tavily {path} timed out: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TavilyError(
                TavilyErrorCode.UPSTREAM, f"tavily {path} transport error: {exc}"
            ) from exc
        if response.status_code == 202:
            try:
                return response.json()
            except ValueError:
                return {}
        return self._handle(path, response, 200, start)

    def _handle(
        self,
        path: str,
        response: httpx.Response,
        expected_status: int,
        start: float,
    ) -> dict[str, Any]:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        if response.status_code != expected_status and not (
            expected_status == 200 and response.status_code == 200
        ):
            raise self._translate(path, response)
        try:
            body = response.json()
        except ValueError as exc:
            raise TavilyError(
                TavilyErrorCode.INVALID_RESPONSE,
                f"tavily {path} returned non-JSON body",
            ) from exc
        if not isinstance(body, dict):
            raise TavilyError(
                TavilyErrorCode.INVALID_RESPONSE,
                f"tavily {path} returned a non-object body",
            )
        usage = body.get("usage") or {}
        logger.info(
            "tavily_request",
            endpoint=path,
            status_code=response.status_code,
            latency_ms=latency_ms,
            request_id=body.get("request_id"),
            credits_used=usage.get("credits_used"),
            result_count=len(body.get("results", []) or []),
        )
        return body

    def _translate(self, path: str, response: httpx.Response) -> TavilyError:
        status = response.status_code
        try:
            payload = response.json()
            message = str(payload.get("detail") or payload.get("error") or payload)
            request_id = payload.get("request_id")
        except ValueError:
            message, request_id = response.text[:500], None
        retry_after = self._retry_after(response)
        if status in (401, 403):
            code = TavilyErrorCode.AUTHENTICATION
        elif status == 402:
            code = TavilyErrorCode.CREDIT_LIMIT
        elif status == 429:
            code = TavilyErrorCode.RATE_LIMIT
        elif status in (400, 422, 404):
            code = (
                TavilyErrorCode.UNSUPPORTED_URL
                if _UNSUPPORTED_RE.search(message)
                else TavilyErrorCode.INVALID_REQUEST
            )
        elif 500 <= status <= 599:
            code = TavilyErrorCode.UPSTREAM
        else:
            code = TavilyErrorCode.UPSTREAM
        logger.warning(
            "tavily_request_failed",
            endpoint=path,
            status_code=status,
            code=code.value,
            request_id=request_id,
        )
        return TavilyError(
            code,
            f"tavily {path} failed: HTTP {status}",
            request_id=request_id,
            retry_after_seconds=retry_after,
        )

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        raw = response.headers.get("retry-after")
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    @staticmethod
    def _parse(model_cls: Any, body: dict[str, Any], path: str) -> Any:
        try:
            return model_cls.model_validate(body)
        except ValidationError as exc:
            raise TavilyError(
                TavilyErrorCode.INVALID_RESPONSE,
                f"tavily {path} returned an unexpected shape",
                request_id=body.get("request_id"),
            ) from exc
