"""Public documentation source backed by Tavily (Map/Crawl/Extract).

Tavily is the ingestion layer only — results flow through the same
hash-skip → chunk → embed → upsert → baseline path as ``SyncService`` and
produce the same ``SyncResult`` field contract, so Stage 1 wiring is a
drop-in. Private repository content is never sent to Tavily: this source
only handles public URLs.

Spec: docs/superpowers/specs/2026-09-20-tavily-rag-design.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import structlog

from draftly.documentation.baseline import create_baseline
from draftly.documentation.chunker import chunk_document
from draftly.documentation.page_type import derive_page_type
from draftly.documentation.parser import parse_markdown
from draftly.documentation.source_models import (
    DiscoveryResult,
    PublicDocumentationConfig,
    SourceDocument,
    SourceType,
)
from draftly.documentation.sync_service import ProgressCallback, SyncResult
from draftly.integrations.tavily.errors import TavilyError, TavilyErrorCode
from draftly.memory.models.document import Document
from draftly.memory.repository import MemoryNamespaces

logger = structlog.get_logger("draftly.documentation.tavily_source")

_PROCESS_CONCURRENCY = 4


def canonicalize_url(url: str) -> str | None:
    """Normalize a URL for dedupe/comparison; None when unusable."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or ""
    query = f"?{parts.query}" if parts.query else ""
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def parse_source_date(raw: Any) -> datetime | None:
    """Parse an API-provided date string; None when absent/unparseable."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def build_research_shards(
    pages: list[SourceDocument],
    *,
    max_files: int = 5,
    max_words: int = 80_000,
) -> list[list[SourceDocument]]:
    """Pack pages into deterministic shards bounded by file/word caps."""
    shards: list[list[SourceDocument]] = []
    current: list[SourceDocument] = []
    current_words = 0
    for page in sorted(pages, key=lambda p: p.source_id):
        words = len(page.content.split())
        if current and (len(current) >= max_files or current_words + words > max_words):
            shards.append(current)
            current, current_words = [], 0
        current.append(page)
        current_words += words
    if current:
        shards.append(current)
    return shards


def group_chunks_into_pages(
    chunks: list[dict[str, Any]],
) -> tuple[list[SourceDocument], list[str]]:
    """Group recalled chunks into page-level SourceDocuments.

    Returns (pages, orphan_chunk_ids). Chunks without a usable source_url
    cannot be attributed to a page and are reported as orphans.
    """
    from urllib.parse import urlsplit as _urlsplit

    by_url: dict[str, list[dict[str, Any]]] = {}
    orphans: list[str] = []
    for chunk in chunks:
        meta = chunk.get("metadata") or {}
        url = meta.get("source_url") or meta.get("source_id")
        if not url:
            orphans.append(chunk.get("id", "unknown"))
            continue
        by_url.setdefault(url, []).append(chunk)
    pages: list[SourceDocument] = []
    for url, group in by_url.items():
        group.sort(key=lambda c: c.get("start_line", 0) or 0)
        content = "\n\n".join(
            c.get("content", "") for c in group if c.get("content")
        )
        if not content.strip():
            orphans.extend(c.get("id", "unknown") for c in group)
            continue
        first_meta = group[0].get("metadata") or {}
        parts = _urlsplit(url)
        title = first_meta.get("title") or f"{parts.netloc}{parts.path}"
        try:
            pages.append(
                SourceDocument(
                    source_id=url,
                    path=parts.path or "/",
                    title=title,
                    content=content,
                    source_url=url,
                    metadata={
                        "page_type": first_meta.get("page_type")
                        or derive_page_type(url),
                        "chunk_ids": [c.get("id") for c in group],
                    },
                )
            )
        except Exception:
            orphans.extend(c.get("id", "unknown") for c in group)
    return pages, orphans


class TavilyDocumentationSource:
    """Ingest public documentation via Tavily into the docs namespace."""

    def __init__(self, client: Any, *, documents: Any = None, memory: Any = None) -> None:
        self.client = client
        self.documents = documents
        self.memory = memory

    # ------------------------------------------------------------------
    # Discovery (Stage 0)
    # ------------------------------------------------------------------

    async def discover(self, config: PublicDocumentationConfig) -> DiscoveryResult:
        root = str(config.root_url)
        root_canonical = canonicalize_url(root)
        if root_canonical is None:
            raise TavilyError(
                TavilyErrorCode.INVALID_REQUEST,
                f"unusable documentation root: {root}",
            )
        root_host = urlsplit(root_canonical).netloc
        resp = await self.client.map(
            root,
            max_depth=3,
            select_paths=config.include_paths or (),
            exclude_paths=config.exclude_paths or (),
            allow_external=False,
        )
        candidates: list[str] = []
        seen: set[str] = set()
        skipped = 0
        for raw in resp.urls:
            canonical = canonicalize_url(raw)
            if canonical is None or urlsplit(canonical).netloc != root_host:
                skipped += 1
                continue
            if canonical in seen:
                skipped += 1
                continue
            seen.add(canonical)
            candidates.append(canonical)
        logger.info(
            "tavily_discover",
            root=root_canonical,
            candidates=len(candidates),
            skipped=skipped,
        )
        return DiscoveryResult(
            source_type=SourceType.PUBLIC_DOCUMENTATION,
            candidates=candidates,
            total=len(candidates),
            skipped=skipped,
        )

    # ------------------------------------------------------------------
    # Sync (Stage 1)
    # ------------------------------------------------------------------

    async def sync(
        self,
        *,
        org_id: str,
        config: PublicDocumentationConfig,
        on_progress: ProgressCallback | None = None,
    ) -> SyncResult:
        root = str(config.root_url).rstrip("/")
        crawl = await self.client.crawl(
            root,
            max_depth=3,
            select_paths=config.include_paths or (),
            exclude_paths=config.exclude_paths or (),
            extract_depth="advanced",
            format="markdown",
        )
        contents: dict[str, str] = {}
        updated_at: dict[str, datetime | None] = {}
        order: list[str] = []
        for page in crawl.results:
            canonical = canonicalize_url(page.url)
            if canonical is None or canonical in contents:
                continue
            order.append(canonical)
            contents[canonical] = page.content or ""
            updated_at[canonical] = parse_source_date(
                getattr(page, "published_date", None)
            )

        # Retry eligible (empty) pages via extract (client batches ≤ 20).
        empty = [u for u in order if not contents[u]]
        if empty:
            extracted = await self.client.extract(
                empty, extract_depth="advanced", format="markdown"
            )
            for item in extracted.results:
                canonical = canonicalize_url(item.url)
                if canonical is None:
                    continue
                if item.raw_content:
                    contents[canonical] = item.raw_content
                    if updated_at.get(canonical) is None:
                        updated_at[canonical] = parse_source_date(
                            getattr(item, "published_date", None)
                        )

        result = await self.refresh_pages(
            org_id,
            repository=root,
            items=[
                (u, contents[u], updated_at.get(u)) for u in order if contents[u]
            ],
            failed=[u for u in order if not contents[u]],
            include=config.include_paths,
            exclude=config.exclude_paths,
            on_progress=on_progress,
        )

        if result.document_count == 0 and result.failed_files:
            raise TavilyError(
                TavilyErrorCode.UPSTREAM,
                f"tavily sync stored zero documents with {len(result.failed_files)} failures",
            )
        logger.info(
            "tavily_sync",
            org_id=org_id,
            root=root,
            documents=result.document_count,
            skipped=result.skipped_count,
            failed=len(result.failed_files),
        )
        return result

    async def refresh_pages(
        self,
        org_id: str,
        *,
        repository: str,
        items: list[tuple[str, str, datetime | None]],
        failed: list[str] | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> SyncResult:
        """Hash-skip → chunk → embed → upsert → baseline for page items.

        Shared by full ``sync()`` and targeted freshness refreshes. Items
        are (canonical_url, markdown, source_updated_at) triples; ``failed``
        carries isolated per-URL failures recorded without failing the
        successful pages.
        """
        result = SyncResult(commit_sha="", repository=repository)
        sem = asyncio.Semaphore(_PROCESS_CONCURRENCY)
        result.failed_files.extend(failed or [])

        async def _worker(
            url: str, content: str, updated: datetime | None
        ) -> None:
            async with sem:
                try:
                    await self._process(
                        org_id=org_id, url=url, result=result,
                        content=content,
                        source_updated_at=updated,
                        on_progress=on_progress,
                    )
                except Exception:
                    logger.exception("tavily_sync_file_failed", url=url)
                    result.failed_files.append(url)

        await asyncio.gather(
            *(_worker(url, content, updated) for url, content, updated in items)
        )

        result.commit_sha = self._fingerprint(
            [url for url, _, _ in items],
            {url: content for url, content, _ in items},
        )
        result.baseline = create_baseline(
            commit_sha=result.commit_sha,
            repository=repository,
            document_count=result.document_count,
            section_count=result.section_count,
            chunk_count=result.chunk_count,
            include=include,
            exclude=exclude,
        )
        return result

    async def _process(
        self,
        *,
        org_id: str,
        url: str,
        result: SyncResult,
        content: str,
        source_updated_at: datetime | None,
        on_progress: ProgressCallback | None,
    ) -> None:
        if not content:
            return
        result.last_committed_dates.append(source_updated_at)

        # Content-hash skip against persisted source_hash (mirror SyncService:
        # same unstripped sha256, orphan rows reprocess instead of skipping).
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        existing = await self.documents.get_by_org_and_path(org_id=org_id, path=url)
        if existing and existing.get("source_hash") == content_hash:
            existing_meta = existing.get("metadata") or {}
            if isinstance(existing_meta, str):
                try:
                    existing_meta = json.loads(existing_meta)
                except (json.JSONDecodeError, TypeError):
                    existing_meta = {}
            if (existing_meta.get("chunk_count") or 0) > 0:
                result.skipped_count += 1
                return

        parse_result = parse_markdown(content)
        chunks = chunk_document(parse_result, content)
        page_type = derive_page_type(url)
        indexed_at = datetime.now(UTC).isoformat()
        title = parse_result.title or f"{urlsplit(url).netloc}{urlsplit(url).path}"

        document_record = await self.documents.upsert(
            org_id=org_id,
            repository=result.repository,
            path=url,
            title=title,
            content=content,
            status="indexed",
            source_hash=content_hash,
            last_committed_at=source_updated_at,
            metadata={
                "source_url": url,
                "source_type": SourceType.PUBLIC_DOCUMENTATION.value,
                "page_type": page_type,
                "section_count": len(parse_result.headings),
                "chunk_count": len(chunks),
            },
        )
        document_id = document_record["id"]

        if chunks:
            await self.memory.delete_by_metadata(
                namespace=MemoryNamespaces.DOCUMENTS,
                key="document_id",
                value=document_id,
                org_id=org_id,
            )
            items = [
                Document(
                    namespace=MemoryNamespaces.DOCUMENTS,
                    memory_type="document_chunk",
                    content=chunk.content,
                    importance=0.5,
                    confidence=0.5,
                    org_id=org_id,
                    path=url,
                    heading_path=chunk.heading_path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    metadata={
                        "document_id": document_id,
                        "path": url,
                        "heading_path": chunk.heading_path,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                        "source_type": SourceType.PUBLIC_DOCUMENTATION.value,
                        "source_id": url,
                        "source_url": url,
                        "page_type": page_type,
                        "section": chunk.heading_path,
                        "content_hash": content_hash,
                        "indexed_at": indexed_at,
                    },
                )
                for chunk in chunks
            ]
            await self.memory.store_batch(items)

        result.document_count += 1
        result.section_count += len(parse_result.headings)
        result.chunk_count += len(chunks)
        if on_progress is not None:
            on_progress(result.document_count, result.chunk_count)

    @staticmethod
    def _fingerprint(order: list[str], contents: dict[str, str]) -> str:
        hashes = sorted(
            hashlib.sha256(contents[u].encode()).hexdigest()
            for u in order
            if contents.get(u)
        )
        return "public:" + hashlib.sha256("|".join(hashes).encode()).hexdigest()[:16]


def files_for_shard(shard: list[SourceDocument]) -> list[dict[str, str]]:
    """Encode a research shard as Tavily ``files[]`` entries (base64 .md)."""
    import base64

    files = []
    for i, page in enumerate(shard):
        raw = f"# {page.title}\n\nSource: {page.source_url}\n\n{page.content}"
        files.append(
            {
                "filename": f"shard-{i}.md",
                "content_b64": base64.b64encode(raw.encode()).decode(),
            }
        )
    return files


def pack_sample_files(
    items: list[tuple[str, str]],
    *,
    max_files: int = 5,
    max_words: int = 80_000,
) -> list[dict[str, str]]:
    """Pack (title, content) pairs into Tavily ``files[]`` entries.

    Fills each file up to ``max_words`` words, at most ``max_files`` files;
    the remainder is dropped. Deterministic (input order kept).
    """
    import base64

    files: list[dict[str, str]] = []
    current: list[str] = []
    current_words = 0

    def _flush() -> None:
        if current:
            raw = "\n\n---\n\n".join(current)
            files.append(
                {
                    "filename": f"sample-{len(files)}.md",
                    "content_b64": base64.b64encode(raw.encode()).decode(),
                }
            )

    for title, content in items:
        if len(files) >= max_files:
            break
        words = len(content.split())
        if current and current_words + words > max_words:
            _flush()
            current.clear()
            current_words = 0
            if len(files) >= max_files:
                break
        current.append(f"# {title}\n\n{content}")
        current_words += words
    _flush()
    return files


ShardProgressCallback = Callable[[int, int], None]
