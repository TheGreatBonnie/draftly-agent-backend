"""Knowledge read repository contract tests."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from draftly.app.api.knowledge_schemas import KnowledgeListItem
from draftly.persistence.repositories.knowledge import KnowledgeRepository


class FakeDatabaseClient:
    async def fetch_one(self, query: str, *params: Any) -> dict[str, Any] | None:
        return None


class FakeEmbedder:
    async def embed(self, text: str) -> list[float]:
        return [0.0]


def test_knowledge_list_item_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        KnowledgeListItem(
            id="item-1",
            entity="Fact",
            description=None,
            status="connected",
            importance=0.5,
            confidence=0.5,
            created_at=None,
            updated_at=None,
            namespace="knowledge",
            memory_type="knowledge",
        )


def test_knowledge_repository_requires_org_id() -> None:
    repository = KnowledgeRepository(client=FakeDatabaseClient(), embedder=FakeEmbedder())
    with pytest.raises(ValueError, match="org_id"):
        repository._require_org_id("")
