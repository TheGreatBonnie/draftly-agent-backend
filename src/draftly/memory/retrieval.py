"""Memory retrieval (plan §8.1) — semantic search + relevance scoring."""

from __future__ import annotations

import math
from typing import Any

from draftly.memory.ranking import MemoryRanking
from draftly.memory.repository import DomainMemoryRepository


class MemoryRetrieval:
    """Retrieve and score memory records for a query."""

    def __init__(
        self,
        repository: DomainMemoryRepository | None = None,
        ranking: MemoryRanking | None = None,
    ) -> None:
        self.repository = repository or DomainMemoryRepository()
        self.ranking = ranking or MemoryRanking()

    async def retrieve(
        self,
        *,
        namespace: str,
        query: str,
        limit: int = 10,
        min_similarity: float = 0.0,
        org_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search, then re-rank by recency/importance/quality.

        The query is embedded exactly once and reused for both the DB search
        and the in-process similarity pass — previously the identical query
        was embedded once per candidate (a 1 + N embedding amplification).
        """
        query_embedding = await self.repository.embeddings.embed(query)
        candidates = await self.repository.search(
            namespace=namespace,
            query=query,
            limit=max(limit * 3, 10),
            org_id=org_id,
            embedding=query_embedding,
        )
        for record in candidates:
            record["similarity"] = _cosine(record.get("embedding"), query_embedding)
        if min_similarity > 0:
            candidates = [r for r in candidates if r["similarity"] >= min_similarity]
        return self.ranking.rank(candidates, limit=limit)

    async def retrieve_multi(
        self,
        *,
        namespaces: list[str],
        query: str,
        per_namespace: int = 5,
        org_id: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fan out across namespaces (e.g. knowledge + solutions)."""
        results: dict[str, list[dict[str, Any]]] = {}
        for namespace in namespaces:
            results[namespace] = await self.retrieve(
                namespace=namespace,
                query=query,
                limit=per_namespace,
                org_id=org_id,
            )
        return results


def _cosine(a: Any, b: list[float]) -> float:
    """Cosine similarity; 0.0 when embeddings are missing/mismatched."""
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (norm_a * norm_b)
