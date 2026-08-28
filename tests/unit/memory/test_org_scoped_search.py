import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from draftly.integrations.database.vector_search import VectorSearch
from draftly.memory.service import MemoryService


@pytest.mark.asyncio
async def test_vector_search_org_scoped():
    client_mock = AsyncMock()
    search = VectorSearch(client=client_mock)
    client_mock.fetch_all.return_value = []

    await search.search(namespace="docs", embedding=[0.1, 0.2], limit=5, org_id="org-1")

    call_args = client_mock.fetch_all.call_args[0]
    sql = call_args[0]
    assert "mi.org_id = $3" in sql
    assert call_args[3] == "org-1"

@pytest.mark.asyncio
async def test_memory_service_recall_org_scoped():
    repo_mock = MagicMock()
    repo_mock.search = AsyncMock(return_value=[])
    repo_mock.embeddings.embed = AsyncMock(return_value=[0.1, 0.2])
    service = MemoryService(repository=repo_mock)

    await service.recall(namespace="docs", query="test", limit=5, org_id="org-1")

    repo_mock.search.assert_awaited_once_with(
        namespace="docs",
        query="test",
        limit=15,
        org_id="org-1",
        embedding=[0.1, 0.2],
    )

def test_migration_exists():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "src"
        / "draftly"
        / "persistence"
        / "migrations"
        / "035_memory_org_and_meta.sql"
    )
    assert os.path.exists(path)
    with open(path) as f:
        content = f.read()
    assert "idx_memory_embeddings_org" in content
    assert "idx_memory_items_org_namespace" in content
    assert "idx_memory_items_metadata_gin" in content
