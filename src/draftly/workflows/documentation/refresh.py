"""Targeted public-docs freshness refreshes (PR-merged / release / manual).

Re-extracts changed URLs with content-hash semantics (equal → skip,
differs → replace), bounded retries absorbing deploy lag, and deletion of
pages still unavailable after retries. A page that was never indexed stays
failed — it is never deleted. Non-retryable failures never delete either.

Flag-gated and inert without a configured public corpus; private sources
never reach Tavily here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

from draftly.documentation.sync_service import ProgressCallback
from draftly.memory.repository import MemoryNamespaces

logger = structlog.get_logger("draftly.workflows.documentation.refresh")


@dataclass
class RefreshResult:
    skipped: int = 0
    replaced: int = 0
    failed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)


class _AbortUrlError(Exception):
    """Non-retryable extract failure: record failed, never delete."""

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(url)


def _refresh_enabled(config: Any) -> bool:
    return getattr(config, "tavily_public_ingestion_enabled", False) is True


async def _extract_with_retry(
    client: Any,
    url: str,
    *,
    max_retries: int,
    base_seconds: float,
) -> tuple[str, Any]:
    """Extract one URL; ("", None) when unavailable after retries.

    Raises _AbortUrlError on non-retryable failures (record failed, never
    delete) and TavilyError on credit-limit (halt everything).
    """
    from draftly.documentation.tavily_source import parse_source_date
    from draftly.integrations.tavily.errors import (
        TavilyError,
        TavilyErrorCode,
        is_retryable,
    )

    delay = base_seconds
    for attempt in range(max_retries + 1):
        try:
            resp = await client.extract(
                [url], extract_depth="advanced", format="markdown"
            )
        except TavilyError as exc:
            if exc.code == TavilyErrorCode.CREDIT_LIMIT:
                raise
            if not is_retryable(exc.code):
                raise _AbortUrlError(url) from exc
            if attempt >= max_retries:
                return "", None
            await asyncio.sleep(
                exc.retry_after_seconds
                if exc.retry_after_seconds is not None
                else delay
            )
            delay = min(delay * 2, 20.0)
            continue
        if not resp.results:
            if attempt >= max_retries:
                return "", None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 20.0)
            continue
        item = resp.results[0]
        content = item.raw_content or ""
        if content or attempt >= max_retries:
            return content, parse_source_date(
                getattr(item, "published_date", None)
            )
        await asyncio.sleep(delay)
        delay = min(delay * 2, 20.0)
    return "", None


async def refresh_public_documentation(
    *,
    documents: Any,
    memory: Any,
    config: Any,
    settings: Any = None,
    org_id: str,
    urls: list[str],
    max_retries: int = 3,
    retry_base_seconds: float = 1.0,
    on_progress: ProgressCallback | None = None,
) -> RefreshResult:
    """Re-extract the given public URLs with hash-skip/replace/delete.

    ``config`` is the corpus (PublicDocumentationConfig); ``settings``
    carries the Tavily client fields and feature flag (defaults to
    ``config`` when the corpus object already has them).
    """
    from draftly.documentation.tavily_source import (
        TavilyDocumentationSource,
        canonicalize_url,
    )
    from draftly.integrations.tavily.client import TavilyClient

    result = RefreshResult()
    settings = settings if settings is not None else config
    if not _refresh_enabled(settings):
        return result

    root_cfg = getattr(config, "root_url", None)
    targets: list[str] = []
    for raw in urls or []:
        canonical = canonicalize_url(raw)
        if canonical is None:
            result.failed.append(raw)
        else:
            targets.append(canonical)
    if not targets:
        return result

    client = TavilyClient(
        getattr(settings, "tavily_api_key", None) or "",
        base_url=getattr(settings, "tavily_base_url", "https://api.tavily.com"),
        timeout_seconds=getattr(settings, "tavily_request_timeout_seconds", 60),
        max_concurrency=getattr(settings, "tavily_max_concurrency", 4),
    )
    source = TavilyDocumentationSource(
        client, documents=documents, memory=memory
    )
    try:
        items: list[tuple[str, str, Any]] = []
        empties: list[str] = []
        for url in targets:
            try:
                content, updated = await _extract_with_retry(
                    client,
                    url,
                    max_retries=max_retries,
                    base_seconds=retry_base_seconds,
                )
            except _AbortUrlError:
                result.failed.append(url)
                continue
            if content:
                items.append((url, content, updated))
            else:
                empties.append(url)
        # Config include/exclude shape: PublicDocumentationConfig lists, or
        # a plain settings object without them.
        include = list(getattr(config, "include_paths", None) or [])
        exclude = list(getattr(config, "exclude_paths", None) or [])
        repository = str(root_cfg).rstrip("/") if root_cfg else targets[0]
        sync_result = await source.refresh_pages(
            org_id,
            repository=repository,
            items=items,
            include=include,
            exclude=exclude,
            on_progress=on_progress,
        )
        result.skipped = sync_result.skipped_count
        result.replaced = sync_result.document_count
        result.failed.extend(sync_result.failed_files)
        for url in empties:
            row = await documents.get_by_org_and_path(org_id=org_id, path=url)
            if row and row.get("id"):
                await memory.delete_by_metadata(
                    namespace=MemoryNamespaces.DOCUMENTS,
                    key="document_id",
                    value=row["id"],
                    org_id=org_id,
                )
                await documents.delete(row["id"])
                result.deleted.append(url)
            else:
                result.failed.append(url)
    finally:
        await client.aclose()
    logger.info(
        "public_docs_refresh",
        org_id=org_id,
        skipped=result.skipped,
        replaced=result.replaced,
        failed=len(result.failed),
        deleted=len(result.deleted),
    )
    return result


async def handle_pr_merged_for_docs(
    context: Any,
    org_id: str,
    event: dict[str, Any] | None,
    *,
    config: Any = None,
) -> RefreshResult | None:
    """Refresh the changed doc URLs carried by a merged-PR/release event.

    Returns None when inert (no URLs, flag off, or no public corpus).
    """
    from draftly.tools.search._rag import resolve_public_config

    urls = list((event or {}).get("doc_urls") or [])
    if not urls:
        return None
    settings = getattr(context, "config", None)
    if not _refresh_enabled(settings):
        return None
    resolved = config
    if resolved is None:
        resolved = await resolve_public_config(org_id)
    if resolved is None:
        return None
    return await refresh_public_documentation(
        documents=context.repositories.documents,
        memory=context.memory,
        config=resolved,
        settings=settings,
        org_id=org_id,
        urls=urls,
    )
