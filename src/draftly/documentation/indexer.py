"""Documentation indexer (plan §8.4) — build/update the doc index."""

from __future__ import annotations

from typing import Any

import structlog

from draftly.documentation.analyzer import DocumentationAnalyzer
from draftly.persistence.repositories.documents import DocumentRepository

logger = structlog.get_logger(__name__)


class DocumentationIndexer:
    """Upsert documents into the index with extracted metadata."""

    def __init__(
        self,
        repository: DocumentRepository | None = None,
        analyzer: DocumentationAnalyzer | None = None,
    ) -> None:
        self.repository = repository or DocumentRepository()
        self.analyzer = analyzer or DocumentationAnalyzer()

    async def index_document(
        self,
        *,
        org_id: str,
        repository: str,
        path: str,
        content: str,
        title: str | None = None,
        document_type: str = "markdown",
    ) -> dict[str, Any]:
        """Index one document; topics/keywords go into metadata."""
        resolved_title = title or path.rsplit("/", 1)[-1]
        metadata = {
            "topics": self.analyzer.topics(content),
            "keywords": self.analyzer.keywords(content),
            "links": self.analyzer.links(content),
            "repository": repository,
        }
        record = await self.repository.create(
            org_id=org_id,
            path=path,
            title=resolved_title,
            content=content,
            document_type=document_type,
            metadata=metadata,
        )
        logger.debug("document_indexed path=%s", path)
        return record

    async def reindex_repository(self, repository: str) -> int:
        """Refresh metadata for every indexed document of a repo."""
        docs = await self.repository.find_by_repository(repository=repository)
        count = 0
        for doc in docs:
            await self.repository.update(
                doc["id"],
                metadata={
                    **(doc.get("metadata") or {}),
                    "topics": self.analyzer.topics(doc.get("content", "")),
                    "keywords": self.analyzer.keywords(doc.get("content", "")),
                },
            )
            count += 1
        return count
