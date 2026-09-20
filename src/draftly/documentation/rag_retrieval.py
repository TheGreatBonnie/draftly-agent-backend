"""RAG retrieval over the indexed docs namespace (pgvector + full-text).

Single entry point for both workflows (onboarding stages 2/3/5, PR
doc-retrieval tools). Tavily live search is the fallback when the index is
stale or insufficient — never the primary path.

Score blend (spec)::
    final = 0.60*vector + 0.25*full_text + 0.10*exact_id + 0.05*page_type
Routing (spec): >=0.78 local only; 0.60-0.78 combine; <0.60 live-or-abstain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

import structlog

from draftly.documentation.page_type import map_question_type

logger = structlog.get_logger("draftly.documentation.rag_retrieval")

LOCAL_ONLY_THRESHOLD = 0.78
COMBINE_THRESHOLD = 0.60

_SQL = """
SELECT
  mi.id::text                       AS id,
  mi.namespace,
  mi.content,
  mi.metadata,
  1 - (me.embedding <=> $1::vector) AS similarity,
  ts_rank(mi.content_search_vector, plainto_tsquery('english', $2)) AS fts_rank,
  mi.metadata->>'source_id'         AS source_id,
  mi.metadata->>'page_type'         AS page_type,
  mi.metadata->>'source_url'        AS source_url
FROM memory_embeddings me
JOIN memory_items mi ON mi.id = me.memory_item_id
WHERE mi.namespace = $3
  AND mi.org_id = $4
  AND mi.status = 'active'
  AND ($5::text IS NULL OR mi.metadata->>'product' = $5)
  AND ($6::text IS NULL OR mi.metadata->>'version' = $6)
  AND ($7::text IS NULL OR mi.metadata->>'source_type' = $7)
ORDER BY similarity DESC
LIMIT $8
"""


@dataclass(frozen=True)
class RagResult:
    results: list[dict[str, Any]]
    confidence: float
    source: Literal["local", "tavily", "none"]
    question_type: str = "general"


@dataclass
class RagRetrieval:
    db: Any
    embeddings: Any
    tavily_client: Any = None
    live_fallback_enabled: bool = False
    public_config: Any = None
    low_confidence_topics: list[str] = field(default_factory=list)

    async def retrieve(
        self,
        *,
        org_id: str,
        query: str,
        product: str | None = None,
        version: str | None = None,
        source_type: str | None = None,
        question_type: str = "general",
        limit: int = 8,
    ) -> RagResult:
        embedding = await self.embeddings.embed(query)
        rows = await self.db.fetch_all(
            _SQL,
            embedding,
            query,
            "documents",
            org_id,
            product,
            version,
            source_type,
            limit * 3,
        )
        scored = [self._score(row, question_type, query) for row in rows]
        scored.sort(key=lambda r: r["score"], reverse=True)
        local = scored[:limit]
        confidence = local[0]["score"] if local else 0.0

        if local and confidence >= LOCAL_ONLY_THRESHOLD:
            return RagResult(
                results=local,
                confidence=confidence,
                source="local",
                question_type=question_type,
            )
        if self._live_available():
            combined = await self._with_live(
                local, org_id=org_id, query=query, product=product, limit=limit
            )
            if combined:
                return RagResult(
                    results=combined,
                    confidence=confidence,
                    source="tavily",
                    question_type=question_type,
                )
            return RagResult(
                results=local,
                confidence=confidence,
                source="none",
                question_type=question_type,
            )
        if local and confidence >= COMBINE_THRESHOLD:
            source: Literal["local", "tavily", "none"] = "local"
        else:
            source = "none"
            if query not in self.low_confidence_topics:
                self.low_confidence_topics.append(query)
        return RagResult(
            results=local,
            confidence=confidence,
            source=source,
            question_type=question_type,
        )

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score(self, row: dict[str, Any], question_type: str, query: str) -> dict:
        similarity = row.get("similarity") or 0.0
        fts = row.get("fts_rank") or 0.0
        source_id = row.get("source_id") or ""
        exact = (
            1.0
            if (source_id == query or source_id.rstrip("/") == query.rstrip("/"))
            else 0.0
        )
        priorities = map_question_type(question_type)
        page_priority = 1.0 if (row.get("page_type") or "index") in priorities else 0.40
        score = (
            0.60 * similarity + 0.25 * fts + 0.10 * exact + 0.05 * page_priority
        )
        metadata = row.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        url = row.get("source_url") or metadata.get("source_url")
        return {
            "id": row.get("id"),
            "content": row.get("content", ""),
            "metadata": metadata,
            "url": url,
            "source_url": url,
            "source_id": source_id,
            "page_type": row.get("page_type") or "index",
            "score": round(score, 6),
        }

    # ------------------------------------------------------------------
    # Live fallback
    # ------------------------------------------------------------------

    def _live_available(self) -> bool:
        return bool(
            self.live_fallback_enabled
            and self.tavily_client is not None
            and self.public_config is not None
        )

    def _corpus_host(self) -> str:
        return urlsplit(str(self.public_config.root_url)).netloc.lower()

    def _corpus_prefix(self) -> str:
        return str(self.public_config.root_url).rstrip("/")

    async def _with_live(
        self,
        local: list[dict],
        *,
        org_id: str,
        query: str,
        product: str | None,
        limit: int,
    ) -> list[dict]:
        label = product or self._corpus_host()
        try:
            response = await self.tavily_client.search(
                f"{label} documentation: {query}",
                search_depth="advanced",
                max_results=limit,
                chunks_per_source=3,
                include_domains=[self._corpus_host()],
                include_domains_mode="restrict",
                include_answer=False,
                include_raw_content=False,
            )
        except Exception:
            logger.exception("rag_live_fallback_failed", org_id=org_id)
            return list(local)
        prefix = self._corpus_prefix()
        seen = {r.get("source_url") for r in local}
        live_rows = []
        for item in response.results:
            url = item.url
            if url in seen:
                continue
            if not (url == prefix or url.startswith(prefix + "/")):
                continue
            seen.add(url)
            live_rows.append(
                {
                    "id": f"tavily:{url}",
                    "content": item.content,
                    "metadata": {
                        "source": "tavily",
                        "source_url": url,
                        "title": item.title,
                    },
                    "url": url,
                    "source_url": url,
                    "source_id": url,
                    "page_type": "index",
                    # No local blend inputs for live rows; they sort after
                    # scored local rows by construction (appended, then cut).
                    "score": 0.0,
                }
            )
        return (local + live_rows)[:limit]
