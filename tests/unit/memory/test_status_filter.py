"""VectorSearch status-filter regression tests."""

import pytest

from draftly.integrations.database.vector_search import VectorSearch


@pytest.mark.asyncio
async def test_search_filters_to_active_status():
    captured = {}

    class FakeClient:
        async def fetch_all(self, query, *args):
            captured["query"] = query
            return []

    searcher = VectorSearch(client=FakeClient())
    await searcher.search(namespace="knowledge", embedding=[0.1] * 4, limit=3)
    assert "mi.status = 'active'" in captured["query"]
