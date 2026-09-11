"""Organization scoping tests for Knowledge's related reads."""

from __future__ import annotations

from typing import Any

import pytest

from draftly.integrations.database.memory_feedback_store import MemoryFeedbackStore
from draftly.integrations.database.memory_links_store import MemoryLinksStore


class RecordingClient:
    def __init__(self) -> None:
        self.query: str | None = None
        self.params: tuple[Any, ...] = ()

    async def fetch_all(self, query: str, *params: Any) -> list[dict[str, Any]]:
        self.query = query
        self.params = params
        return []


@pytest.mark.asyncio
async def test_links_filter_by_org_and_memory_id() -> None:
    client = RecordingClient()

    await MemoryLinksStore(client=client).list_by_memory(
        org_id="org-1", memory_item_id="item-1"
    )

    assert "org_id = $1" in client.query
    assert "source_memory_id = $2" in client.query
    assert "target_memory_id = $2" in client.query
    assert client.params == ("org-1", "item-1")


@pytest.mark.asyncio
async def test_feedback_filters_by_org_and_memory_id() -> None:
    client = RecordingClient()

    await MemoryFeedbackStore(client=client).list_by_memory(
        org_id="org-1", memory_item_id="item-1"
    )

    assert "org_id = $1" in client.query
    assert "memory_item_id = $2" in client.query
    assert client.params == ("org-1", "item-1")
