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


def test_related_projections_remove_internal_columns() -> None:
    source = KnowledgeRepository._source_projection(
        {
            "id": "source-1",
            "org_id": "org-1",
            "memory_item_id": "item-1",
            "source_type": "github",
            "source_id": "file-1",
            "source_url": "https://example.test",
            "repository": "acme/docs",
            "commit_sha": "abc",
            "evidence": "line 1",
        }
    )
    link = KnowledgeRepository._link_projection(
        {
            "id": "link-1",
            "org_id": "org-1",
            "source_memory_id": "item-1",
            "target_memory_id": "item-2",
            "relationship": "related",
            "confidence": 0.8,
            "created_at": "2026-09-10T00:00:00Z",
        }
    )
    feedback = KnowledgeRepository._feedback_projection(
        {
            "id": "feedback-1",
            "org_id": "org-1",
            "memory_item_id": "item-1",
            "feedback_type": "verified",
            "source": "reviewer",
            "score": 1.0,
            "comment": "confirmed",
            "created_at": "2026-09-10T00:00:00Z",
        }
    )

    assert "org_id" not in source and "memory_item_id" not in source
    assert "org_id" not in link and "created_at" not in link
    assert "org_id" not in feedback and "memory_item_id" not in feedback


def test_knowledge_cursor_rejects_invalid_timestamp_and_id() -> None:
    with pytest.raises(ValueError, match="Invalid Knowledge cursor"):
        KnowledgeRepository._decode_cursor("eyJ1cGRhdGVkX2F0IjoiYmFkIiwiaWQiOiJub3QtaWQifQ")
