"""RagRetrieval: hybrid blend, rerank, confidence routing.

Plan: docs/superpowers/plans/2026-09-20-tavily-rag.md (Task 9).
Score blend (spec): 0.60*vector + 0.25*full_text + 0.10*exact + 0.05*page_type.
Routing: >=0.78 local; 0.60-0.78 combine; <0.60 live-or-abstain.
"""

from __future__ import annotations

from typing import Any

import pytest

from draftly.documentation.rag_retrieval import RagRetrieval
from draftly.documentation.source_models import PublicDocumentationConfig
from tests.fakes import FakeTavilyClient

ROOT = "https://docs.example.com"


class FakeRagDb:
    """Scripted fetch_all; records the SQL and params of the last call."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.last_query: str = ""
        self.last_params: tuple = ()

    async def fetch_all(self, query: str, *args: Any) -> list[dict]:
        self.last_query = query
        self.last_params = args
        return self.rows


class FakeEmbeddings:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.1, 0.2, 0.3]


def _row(
    *,
    similarity: float = 0.9,
    fts_rank: float = 0.8,
    source_id: str = "https://docs.example.com/a",
    page_type: str = "reference",
    url: str = "https://docs.example.com/a",
    content: str = "content",
) -> dict:
    return {
        "id": "m1",
        "namespace": "documents",
        "content": content,
        "metadata": {"source_id": source_id},
        "similarity": similarity,
        "fts_rank": fts_rank,
        "source_id": source_id,
        "page_type": page_type,
        "source_url": url,
    }


def _config() -> PublicDocumentationConfig:
    return PublicDocumentationConfig(root_url=ROOT + "/")


def _retrieval(
    rows: list[dict],
    *,
    live: FakeTavilyClient | None = None,
    live_enabled: bool = False,
) -> tuple[RagRetrieval, FakeRagDb, FakeEmbeddings]:
    db = FakeRagDb(rows)
    embeddings = FakeEmbeddings()
    return (
        RagRetrieval(
            db=db,
            embeddings=embeddings,
            tavily_client=live,
            live_fallback_enabled=live_enabled,
            public_config=_config() if live_enabled else None,
        ),
        db,
        embeddings,
    )


@pytest.mark.asyncio
async def test_blend_weights() -> None:
    retrieval, _, _ = _retrieval(
        [_row(similarity=1.0, fts_rank=1.0, source_id="q", page_type="reference")]
    )

    result = await retrieval.retrieve(
        org_id="o", query="q", question_type="signatures"
    )

    assert result.results[0]["score"] == 1.0
    assert result.confidence == 1.0
    assert result.source == "local"


@pytest.mark.asyncio
async def test_blend_weights_partial() -> None:
    retrieval, _, _ = _retrieval(
        [_row(similarity=0.5, fts_rank=0.5, source_id="other", page_type="index")]
    )

    result = await retrieval.retrieve(
        org_id="o", query="q", question_type="signatures"
    )

    # 0.60*0.5 + 0.25*0.5 + 0.10*0 + 0.05*0.40 (index not in {reference})
    assert result.results[0]["score"] == pytest.approx(0.30 + 0.125 + 0.02)


@pytest.mark.asyncio
async def test_exact_source_id_bonus() -> None:
    retrieval, _, _ = _retrieval(
        [
            _row(similarity=0.6, fts_rank=0.6, source_id="near-miss"),
            _row(similarity=0.5, fts_rank=0.5, source_id="exact-id"),
        ]
    )

    result = await retrieval.retrieve(org_id="o", query="exact-id")

    assert result.results[0]["source_id"] == "exact-id"


@pytest.mark.asyncio
async def test_page_type_priority_for_question_type() -> None:
    retrieval, _, _ = _retrieval(
        [
            _row(similarity=0.7, fts_rank=0.7, page_type="reference"),
            _row(similarity=0.7, fts_rank=0.7, page_type="how-to"),
        ]
    )

    result = await retrieval.retrieve(
        org_id="o", query="q", question_type="procedures"
    )

    assert result.results[0]["page_type"] == "how-to"


@pytest.mark.asyncio
async def test_no_rows_returns_source_none() -> None:
    retrieval, _, _ = _retrieval([])

    result = await retrieval.retrieve(org_id="o", query="q")

    assert result.results == []
    assert result.confidence == 0.0
    assert result.source == "none"


@pytest.mark.asyncio
async def test_routing_local_when_ge_078() -> None:
    live = FakeTavilyClient()
    retrieval, _, embeddings = _retrieval(
        [_row(similarity=1.0, fts_rank=1.0, source_id="q")],
        live=live,
        live_enabled=True,
    )

    result = await retrieval.retrieve(org_id="o", query="q")

    assert result.source == "local"
    assert live.calls == []
    assert embeddings.calls == ["q"]  # embedded exactly once


@pytest.mark.asyncio
async def test_routing_combine_when_060_to_078() -> None:
    live = FakeTavilyClient(
        search_results=[
            {"title": "Live", "url": f"{ROOT}/live", "content": "fresh"}
        ]
    )
    retrieval, _, _ = _retrieval(
        [_row(similarity=0.8, fts_rank=0.8, source_id="other")],
        live=live,
        live_enabled=True,
    )

    result = await retrieval.retrieve(org_id="o", query="q", product="Acme")

    # 0.60*0.8 + 0.25*0.8 = 0.68 -> combine band
    assert result.source == "tavily"
    assert [c[0] for c in live.calls] == ["search"]
    urls = [r["source_url"] for r in result.results]
    assert "https://docs.example.com/a" in urls
    assert f"{ROOT}/live" in urls


@pytest.mark.asyncio
async def test_routing_below_060_without_live_abstains() -> None:
    retrieval, _, _ = _retrieval([_row(similarity=0.3, fts_rank=0.2)])

    result = await retrieval.retrieve(org_id="o", query="q")

    assert result.source == "none"
    assert len(result.results) == 1  # rows kept for gap analysis


@pytest.mark.asyncio
async def test_routing_below_060_with_live_uses_live() -> None:
    live = FakeTavilyClient(
        search_results=[{"title": "Live", "url": f"{ROOT}/live", "content": "fresh"}]
    )
    retrieval, _, _ = _retrieval(
        [_row(similarity=0.3, fts_rank=0.2)], live=live, live_enabled=True
    )

    result = await retrieval.retrieve(org_id="o", query="q")

    assert result.source == "tavily"
    assert f"{ROOT}/live" in [r["source_url"] for r in result.results]


@pytest.mark.asyncio
async def test_live_results_filtered_to_corpus_prefix() -> None:
    # Corpus rooted at /docs/: the sibling /blog/ subsite must be rejected
    # even though it shares the host (spec: host restrict + prefix filter).
    docs_root = f"{ROOT}/docs/"
    live = FakeTavilyClient(
        search_results=[
            {"title": "Docs", "url": f"{ROOT}/docs/a", "content": "x"},
            {"title": "Sibling", "url": f"{ROOT}/blog/b", "content": "y"},
        ]
    )
    retrieval = RagRetrieval(
        db=FakeRagDb([]),
        embeddings=FakeEmbeddings(),
        tavily_client=live,
        live_fallback_enabled=True,
        public_config=PublicDocumentationConfig(root_url=docs_root),
    )

    result = await retrieval.retrieve(org_id="o", query="q")

    assert result.source == "tavily"
    assert [r["source_url"] for r in result.results] == [f"{ROOT}/docs/a"]


@pytest.mark.asyncio
async def test_live_search_uses_restricted_params() -> None:
    live = FakeTavilyClient(search_results=[])
    retrieval, _, _ = _retrieval([], live=live, live_enabled=True)

    await retrieval.retrieve(org_id="o", query="q", product="Acme")

    assert live.calls[0][0] == "search"
    assert live.calls[0][1] == "Acme documentation: q"
    kwargs = live.calls[0][2]
    assert kwargs["search_depth"] == "advanced"
    assert kwargs["include_domains"] == ["docs.example.com"]
    assert kwargs["include_domains_mode"] == "restrict"
    assert kwargs["include_answer"] is False
    assert kwargs["include_raw_content"] is False


@pytest.mark.asyncio
async def test_product_version_source_type_filters() -> None:
    retrieval, db, _ = _retrieval([])

    await retrieval.retrieve(
        org_id="o",
        query="q",
        product="Acme",
        version="2.0",
        source_type="public_documentation",
        limit=5,
    )

    assert "ts_rank" in db.last_query
    assert "content_search_vector" in db.last_query
    params = db.last_params
    # (embedding, query, namespace, org_id, product, version, source_type, limit)
    assert params[2] == "documents"
    assert params[3] == "o"
    assert params[4] == "Acme"
    assert params[5] == "2.0"
    assert params[6] == "public_documentation"
    assert params[7] == 5 * 3  # top_k fan-out


@pytest.mark.asyncio
async def test_never_calls_live_for_private_source() -> None:
    live = FakeTavilyClient(
        search_results=[{"title": "L", "url": f"{ROOT}/x", "content": "x"}]
    )
    db = FakeRagDb([_row(similarity=0.3, fts_rank=0.2)])
    retrieval = RagRetrieval(
        db=db,
        embeddings=FakeEmbeddings(),
        tavily_client=live,
        live_fallback_enabled=True,
        public_config=None,  # no public corpus configured
    )

    result = await retrieval.retrieve(org_id="o", query="q")

    assert live.calls == []
    assert result.source == "none"
