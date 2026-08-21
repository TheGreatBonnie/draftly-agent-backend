"""Documentation service (plan §8.4) — high-level doc operations."""

from __future__ import annotations

from typing import Any

from draftly.documentation.analyzer import DocumentationAnalyzer
from draftly.documentation.generator import DocumentationGenerator
from draftly.documentation.indexer import DocumentationIndexer
from draftly.documentation.models import DocumentationGap, ValidationResult
from draftly.documentation.updater import DocumentationUpdater
from draftly.documentation.validator import DocumentationValidator
from draftly.persistence.repositories.documents import DocumentRepository


class DocumentationService:
    """Facade over analyzer/indexer/validator/generator/updater."""

    def __init__(
        self,
        repository: DocumentRepository | None = None,
    ) -> None:
        self.repository = repository or DocumentRepository()
        self.analyzer = DocumentationAnalyzer()
        self.indexer = DocumentationIndexer(self.repository, self.analyzer)
        self.validator = DocumentationValidator(self.analyzer)
        self.generator = DocumentationGenerator()
        self.updater = DocumentationUpdater(self.repository)

    async def documents_for(self, repository: str) -> list[dict[str, Any]]:
        return await self.repository.find_by_repository(repository=repository)

    async def detect_gaps(
        self,
        questions: list[str],
        *,
        repository: str,
        min_occurrences: int = 1,
    ) -> list[DocumentationGap]:
        docs = await self.documents_for(repository)
        return self.analyzer.detect_gaps(
            questions,
            docs,
            min_occurrences=min_occurrences,
        )

    async def validate_repository(self, repository: str) -> list[ValidationResult]:
        docs = await self.documents_for(repository)
        known_paths = {str(d.get("path", "")) for d in docs}
        return [
            self.validator.validate(
                path=str(d.get("path", "")),
                content=str(d.get("content", "")),
                updated_at=d.get("updated_at"),
                known_paths=known_paths,
            )
            for d in docs
        ]

    async def create_document(
        self,
        *,
        org_id: str,
        repository: str,
        title: str,
        sections: list[dict[str, str]],
    ) -> dict[str, Any]:
        content = self.generator.generate(title=title, sections=sections)
        path = self.generator.build_path(repository=repository, title=title)
        return await self.indexer.index_document(
            org_id=org_id,
            repository=repository,
            path=path,
            content=content,
            title=title,
        )
