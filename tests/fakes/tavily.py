"""Deterministic in-memory Tavily fake (no network, no keys).

Mirrors :class:`draftly.integrations.tavily.client.TavilyClient`'s method
surface, records calls, and returns scripted data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeTavilyClient:
    """In-memory Tavily API mock."""

    map_urls: list[str] = field(default_factory=list)
    crawl_pages: dict[str, str] = field(default_factory=dict)  # url -> markdown
    crawl_published: dict[str, str] = field(default_factory=dict)  # url -> ISO date
    extract_pages: dict[str, str] = field(default_factory=dict)  # url -> markdown
    # Per-URL scripted extract responses, popped in order (deploy-lag tests).
    # Falls back to extract_pages once drained.
    extract_sequence: dict[str, list[str]] = field(default_factory=dict)
    search_results: list[dict] = field(default_factory=list)
    research_content: Any = None
    research_status: str = "completed"
    calls: list[tuple] = field(default_factory=list)
    fail_codes: list[str] = field(default_factory=list)  # TavilyErrorCode queue

    async def __aenter__(self) -> FakeTavilyClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def map(self, url: str, **kwargs: Any) -> Any:
        from draftly.integrations.tavily.models import MapResponse

        self.calls.append(("map", url, kwargs))
        self._maybe_fail()
        return MapResponse(urls=list(self.map_urls))

    async def crawl(self, url: str, **kwargs: Any) -> Any:
        from draftly.integrations.tavily.models import CrawlResponse, CrawlResult

        self.calls.append(("crawl", url, kwargs))
        self._maybe_fail()
        return CrawlResponse(
            results=[
                CrawlResult(
                    url=u,
                    content=c,
                    published_date=self.crawl_published.get(u),
                )
                for u, c in self.crawl_pages.items()
            ]
        )

    async def extract(self, urls: list[str], **kwargs: Any) -> Any:
        from draftly.integrations.tavily.models import ExtractResponse, ExtractResult

        self.calls.append(("extract", list(urls), kwargs))
        self._maybe_fail()
        results = []
        for u in urls:
            seq = self.extract_sequence.get(u)
            content = seq.pop(0) if seq else self.extract_pages.get(u, "")
            results.append(ExtractResult(url=u, raw_content=content))
        return ExtractResponse(results=results)

    async def search(self, query: str, **kwargs: Any) -> Any:
        from draftly.integrations.tavily.models import SearchResponse, SearchResult

        self.calls.append(("search", query, kwargs))
        self._maybe_fail()
        return SearchResponse(
            results=[SearchResult(**r) for r in self.search_results]
        )

    async def research(self, **kwargs: Any) -> Any:
        from draftly.integrations.tavily.models import ResearchResponse

        self.calls.append(("research", kwargs))
        self._maybe_fail()
        return ResearchResponse(request_id="req-1", status="pending")

    async def research_poll(
        self, request_id: str, *, poll_timeout_seconds: float = 300.0
    ) -> Any:
        from draftly.integrations.tavily.models import ResearchPollResponse

        self.calls.append(("research_poll", request_id))
        self._maybe_fail()
        return ResearchPollResponse(
            request_id=request_id,
            status=self.research_status,
            content=self.research_content,
        )

    def _maybe_fail(self) -> None:
        if self.fail_codes:
            from draftly.integrations.tavily.errors import TavilyError, TavilyErrorCode

            raise TavilyError(
                TavilyErrorCode(self.fail_codes.pop(0)), "fake tavily failure"
            )
