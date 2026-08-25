"""MemoryService must expose batch/delete passthroughs used by sync_service."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from draftly.memory.repository import DomainMemoryRepository
from draftly.memory.service import MemoryService


def _service() -> tuple[MemoryService, AsyncMock]:
    repository = AsyncMock(spec=DomainMemoryRepository)
    return MemoryService(repository=cast(DomainMemoryRepository, repository)), repository


@pytest.mark.asyncio
async def test_delete_by_metadata_delegates_to_repository():
    service, repository = _service()
    repository.delete_by_metadata = AsyncMock(return_value=3)

    deleted = await service.delete_by_metadata(
        namespace="documents", key="document_id", value="doc-1"
    )

    assert deleted == 3
    repository.delete_by_metadata.assert_awaited_once_with(
        namespace="documents", key="document_id", value="doc-1"
    )


@pytest.mark.asyncio
async def test_store_batch_delegates_to_repository():
    service, repository = _service()
    repository.store_batch = AsyncMock(return_value=[{"id": "m-1"}, {"id": "m-2"}])
    items: list[Any] = [object(), object()]

    records = await service.store_batch(items)

    assert [r["id"] for r in records] == ["m-1", "m-2"]
    repository.store_batch.assert_awaited_once_with(items)
