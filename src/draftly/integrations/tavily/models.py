"""Typed Tavily request/response models.

Shapes follow the Tavily HTTP contract (spec: 2026-09-20-tavily-rag-design).
Unknown fields are ignored so additive API changes do not break parsing.
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, Field


class TavilyUsage(BaseModel):
    total_tokens: int | None = None
    credits_used: float | None = None
    extra_tokens: int | None = None


class SearchResult(BaseModel):
    title: str = ""
    url: str
    content: str = ""
    score: float | None = None
    raw_content: str | None = None
    published_date: str | None = None


class SearchResponse(BaseModel):
    # results is required: a 200 without it is an unexpected shape.
    results: list[SearchResult]
    usage: TavilyUsage | None = None
    request_id: str | None = None


class MapResponse(BaseModel):
    # urls is required: a 200 without it is an unexpected shape.
    urls: list[str]
    limit: int | None = None


class CrawlResult(BaseModel):
    url: str
    content: str = Field(
        default="", validation_alias=AliasChoices("content", "raw_content", "markdown")
    )
    markdown: str | None = None
    # Populated when the API returns a per-page date; None otherwise.
    published_date: str | None = None


class CrawlResponse(BaseModel):
    results: list[CrawlResult]
    usage: TavilyUsage | None = None
    request_id: str | None = None


class ExtractResult(BaseModel):
    url: str
    raw_content: str | None = Field(
        default=None, validation_alias=AliasChoices("raw_content", "content", "markdown")
    )
    # Populated when the API returns a per-page date; None otherwise.
    published_date: str | None = None


class ExtractFailed(BaseModel):
    url: str
    error: str = ""


class ExtractResponse(BaseModel):
    results: list[ExtractResult]
    failed_results: list[ExtractFailed] = Field(default_factory=list)
    usage: TavilyUsage | None = None
    request_id: str | None = None


class ResearchResponse(BaseModel):
    request_id: str
    status: str  # "pending" | "completed" | "failed"
    content: Any = None
    usage: TavilyUsage | None = None


class ResearchPollResponse(BaseModel):
    request_id: str
    status: str  # "pending" | "completed" | "failed"
    content: Any = None
    sources: list[dict[str, str]] = Field(default_factory=list)
    usage: TavilyUsage | None = None
