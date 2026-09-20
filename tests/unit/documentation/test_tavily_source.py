"""TavilyDocumentationSource: discover + sync with hash-skip and safe replace.

Uses FakeTavilyClient (no network). Mirrors the SyncService pipeline contract:
documents.upsert / memory.delete_by_metadata / memory.store_batch /
create_baseline, and the SyncResult field contract.

Plan: docs/superpowers/plans/2026-09-20-tavily-rag.md (Task 6).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from draftly.documentation.source_models import (
    PublicDocumentationConfig,
    SourceDocument,
)
from draftly.documentation.tavily_source import (
    TavilyDocumentationSource,
    build_research_shards,
    group_chunks_into_pages,
    pack_sample_files,
)
from draftly.integrations.tavily.errors import TavilyError
from tests.fakes import FakeTavilyClient

ROOT = "https://docs.example.com"


def _config(**overrides: Any) -> PublicDocumentationConfig:
    base: dict[str, Any] = {"root_url": ROOT + "/"}
    base.update(overrides)
    return PublicDocumentationConfig(**base)


class FakeDocuments:
    def __init__(self, rows: dict[str, dict] | None = None) -> None:
        self.rows: dict[str, dict] = dict(rows or {})
        self.upserts: list[dict] = []

    async def get_by_org_and_path(
        self, *, org_id: str, path: str
    ) -> dict[str, Any] | None:
        return self.rows.get(path)

    async def upsert(self, **kwargs: Any) -> dict[str, Any]:
        self.upserts.append(kwargs)
        record = {"id": f"doc-{len(self.upserts)}", **kwargs}
        self.rows[kwargs["path"]] = record
        return record


class FakeMemory:
    def __init__(self) -> None:
        self.deleted: list[tuple] = []
        self.batches: list[list] = []

    async def delete_by_metadata(
        self, *, namespace: str, key: str, value: str, org_id: str
    ) -> None:
        self.deleted.append((namespace, key, value, org_id))

    async def store_batch(self, items: list) -> list[dict]:
        self.batches.append(items)
        return [{"id": f"m{i}"} for i in range(len(items))]


def _source(
    fake: FakeTavilyClient,
    documents: FakeDocuments | None = None,
    memory: FakeMemory | None = None,
) -> tuple[TavilyDocumentationSource, FakeDocuments, FakeMemory]:
    documents = documents if documents is not None else FakeDocuments()
    memory = memory if memory is not None else FakeMemory()
    return (
        TavilyDocumentationSource(client=fake, documents=documents, memory=memory),
        documents,
        memory,
    )


@pytest.mark.asyncio
async def test_discover_dedupes_and_filters_off_root() -> None:
    fake = FakeTavilyClient(
        map_urls=[
            "https://docs.example.com/a",
            "https://docs.example.com/a#frag",
            "https://docs.example.com/a/",
            "https://other.example.com/x",
            "https://docs.example.com/b",
        ]
    )
    source, _, _ = _source(fake)
    result = await source.discover(_config())

    assert result.candidates == [
        "https://docs.example.com/a",
        "https://docs.example.com/b",
    ]
    assert result.total == 2
    assert result.skipped == 3


@pytest.mark.asyncio
async def test_discover_skips_non_http() -> None:
    fake = FakeTavilyClient(
        map_urls=["https://docs.example.com/a", "ftp://docs.example.com/f", "not-a-url"]
    )
    source, _, _ = _source(fake)
    result = await source.discover(_config())

    assert result.candidates == ["https://docs.example.com/a"]
    assert result.skipped == 2


@pytest.mark.asyncio
async def test_sync_crawl_and_extract_retry_collects_failed() -> None:
    fake = FakeTavilyClient(
        crawl_pages={
            "https://docs.example.com/a": "# A\n\nContent A.",
            "https://docs.example.com/b": "",
        },
        extract_pages={"https://docs.example.com/b": "# B\n\nContent B."},
    )
    source, documents, memory = _source(fake)
    progress: list[tuple[int, int]] = []

    result = await source.sync(
        org_id="org-1",
        config=_config(),
        on_progress=lambda d, c: progress.append((d, c)),
    )

    assert result.document_count == 2
    assert result.chunk_count >= 2
    assert result.failed_files == []
    assert len(documents.upserts) == 2
    assert len(memory.batches) == 2
    assert progress[-1] == (2, result.chunk_count)


@pytest.mark.asyncio
async def test_sync_isolates_per_url_failure() -> None:
    fake = FakeTavilyClient(
        crawl_pages={
            "https://docs.example.com/a": "# A\n\nContent A.",
            "https://docs.example.com/broken": "",
        },
        extract_pages={},
    )
    source, documents, _ = _source(fake)

    result = await source.sync(org_id="org-1", config=_config())

    assert result.document_count == 1
    assert result.failed_files == ["https://docs.example.com/broken"]
    assert len(documents.upserts) == 1


@pytest.mark.asyncio
async def test_sync_hash_skip_skips_unchanged() -> None:
    import hashlib

    content = "# A\n\nContent A."
    # mirror sync_service: sha256 over the raw (unstripped) content bytes
    digest = hashlib.sha256(content.encode()).hexdigest()
    documents = FakeDocuments(
        rows={
            "https://docs.example.com/a": {
                "id": "doc-old",
                "source_hash": digest,
                "metadata": {"chunk_count": 3},
            }
        }
    )
    fake = FakeTavilyClient(
        crawl_pages={"https://docs.example.com/a": content},
    )
    source, _, memory = _source(fake, documents=documents)

    result = await source.sync(org_id="org-1", config=_config())

    assert result.document_count == 0
    assert result.skipped_count == 1
    assert documents.upserts == []
    assert memory.batches == []


@pytest.mark.asyncio
async def test_sync_hash_change_replaces_chunks() -> None:
    documents = FakeDocuments(
        rows={
            "https://docs.example.com/a": {
                "id": "doc-old",
                "source_hash": "stale",
                "metadata": {"chunk_count": 3},
            }
        }
    )
    fake = FakeTavilyClient(
        crawl_pages={"https://docs.example.com/a": "# A\n\nNew content."},
    )
    source, _, memory = _source(fake, documents=documents)

    result = await source.sync(org_id="org-1", config=_config())

    assert result.document_count == 1
    assert result.skipped_count == 0
    # stale chunks removed before the replacement batch is stored
    assert memory.deleted != []
    assert len(memory.batches) == 1


@pytest.mark.asyncio
async def test_sync_zero_stored_with_failures_raises() -> None:
    fake = FakeTavilyClient(
        crawl_pages={"https://docs.example.com/broken": ""},
        extract_pages={},
    )
    source, _, _ = _source(fake)

    with pytest.raises(TavilyError):
        await source.sync(org_id="org-1", config=_config())


@pytest.mark.asyncio
async def test_sync_no_deletion_on_failed_replacement() -> None:
    documents = FakeDocuments(
        rows={
            "https://docs.example.com/a": {
                "id": "doc-old",
                "source_hash": "stale",
                "metadata": {"chunk_count": 2},
            }
        }
    )
    fake = FakeTavilyClient(
        crawl_pages={
            "https://docs.example.com/a": "",
            "https://docs.example.com/b": "# B\n\nContent B.",
        },
        extract_pages={},
    )
    source, _, memory = _source(fake, documents=documents)

    result = await source.sync(org_id="org-1", config=_config())

    assert result.document_count == 1
    assert result.failed_files == ["https://docs.example.com/a"]
    # the failed page's chunks were never deleted; only the good page cycled
    assert len(memory.batches) == 1
    deleted_ids = [d[2] for d in memory.deleted]
    assert "doc-old" not in deleted_ids


@pytest.mark.asyncio
async def test_sync_populates_last_committed_dates_from_source_updated_at() -> None:
    fake = FakeTavilyClient(
        crawl_pages={"https://docs.example.com/a": "# A\n\nContent A."},
        crawl_published={"https://docs.example.com/a": "2026-09-01T00:00:00+00:00"},
    )
    source, _, _ = _source(fake)

    result = await source.sync(org_id="org-1", config=_config())

    assert result.last_committed_dates == [
        datetime(2026, 9, 1, tzinfo=UTC)
    ]


@pytest.mark.asyncio
async def test_sync_chunk_metadata_carries_citation_fields() -> None:
    fake = FakeTavilyClient(
        crawl_pages={
            "https://docs.example.com/guides/deploy": "# Deploy\n\nSteps here."
        },
    )
    source, _, memory = _source(fake)

    await source.sync(org_id="org-1", config=_config())

    chunk = memory.batches[0][0]
    meta = chunk.metadata
    assert meta["source_url"] == "https://docs.example.com/guides/deploy"
    assert meta["source_id"] == "https://docs.example.com/guides/deploy"
    assert meta["source_type"] == "public_documentation"
    assert meta["page_type"] == "how-to"
    assert "indexed_at" in meta
    assert "content_hash" in meta


@pytest.mark.asyncio
async def test_sync_creates_baseline_snapshot() -> None:
    fake = FakeTavilyClient(
        crawl_pages={"https://docs.example.com/a": "# A\n\nContent A."},
    )
    source, _, _ = _source(fake)

    result = await source.sync(org_id="org-1", config=_config())

    assert result.baseline is not None
    assert result.baseline.document_count == 1
    assert result.baseline.chunk_count == result.chunk_count
    assert result.baseline.commit_sha == result.commit_sha


def _page(sid: str, words: int, page_type: str = "reference") -> SourceDocument:
    return SourceDocument(
        source_id=f"https://docs.example.com/{sid}",
        path=f"/{sid}",
        title=sid,
        content=" ".join(f"w{i}" for i in range(words)),
        source_url=f"https://docs.example.com/{sid}",
        metadata={"page_type": page_type},
    )


def test_build_research_shards_respects_file_cap() -> None:
    pages = [_page(f"p{i}", 10) for i in range(6)]

    shards = build_research_shards(pages, max_files=5, max_words=10_000)

    assert [len(s) for s in shards] == [5, 1]


def test_build_research_shards_respects_word_cap() -> None:
    pages = [_page(f"p{i}", 10) for i in range(5)]

    shards = build_research_shards(pages, max_files=5, max_words=25)

    assert [len(s) for s in shards] == [2, 2, 1]


def test_build_research_shards_deterministic_order() -> None:
    pages = [_page(f"p{i}", 10) for i in (3, 1, 2)]

    shards = build_research_shards(pages)

    assert [p.source_id for shard in shards for p in shard] == [
        "https://docs.example.com/p1",
        "https://docs.example.com/p2",
        "https://docs.example.com/p3",
    ]


def test_group_chunks_into_pages_groups_and_orders() -> None:
    chunks = [
        {
            "id": "c2",
            "content": "second",
            "start_line": 10,
            "metadata": {
                "source_url": "https://docs.example.com/a",
                "page_type": "reference",
            },
        },
        {
            "id": "c1",
            "content": "first",
            "start_line": 1,
            "metadata": {
                "source_url": "https://docs.example.com/a",
                "page_type": "reference",
            },
        },
        {
            "id": "c3",
            "content": "other",
            "start_line": 1,
            "metadata": {"source_url": "https://docs.example.com/b"},
        },
        {"id": "c4", "content": "orphan", "metadata": {}},
    ]

    pages, orphans = group_chunks_into_pages(chunks)

    assert orphans == ["c4"]
    by_id = {p.source_id: p for p in pages}
    assert by_id["https://docs.example.com/a"].content == "first\n\nsecond"
    assert by_id["https://docs.example.com/a"].metadata["page_type"] == "reference"
    assert by_id["https://docs.example.com/b"].metadata["page_type"] == "index"


def test_pack_sample_files_single_file_when_small() -> None:
    items = [("A", "alpha beta"), ("B", "gamma delta")]

    files = pack_sample_files(items)

    assert len(files) == 1
    assert files[0]["filename"] == "sample-0.md"


def test_pack_sample_files_caps_files_and_words() -> None:
    import base64

    items = [(f"D{i}", " ".join(f"w{j}" for j in range(30))) for i in range(4)]

    files = pack_sample_files(items, max_files=2, max_words=50)

    assert len(files) == 2
    for f in files:
        words = len(base64.b64decode(f["content_b64"]).decode().split())
        assert words <= 50
