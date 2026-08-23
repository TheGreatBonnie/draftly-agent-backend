"""Memory maintenance (forgetting policies) tests."""

import pytest

from draftly.memory.maintenance import MemoryMaintenance


class FakeMaintenanceClient:
    def __init__(self):
        self.queries: list[tuple[str, tuple]] = []
        self._results: dict[str, str] = {}

    async def fetch_one(self, query, *args):
        self.queries.append((query, args))
        return {"ok": 1}

    async def execute(self, query, *args):
        self.queries.append((query, args))
        return "UPDATE 7"


@pytest.mark.asyncio
async def test_archive_old_episodes_uses_180_day_retention():
    client = FakeMaintenanceClient()
    maint = MemoryMaintenance(client=client)
    count = await maint.archive_old_episodes()
    assert count == 1
    query = client.queries[0][0]
    assert "episode_summary" in query
    assert "'180 days'" in client.queries[0][1][0] or "180" in str(client.queries[0][1])


@pytest.mark.asyncio
async def test_demote_stale_excludes_decisions():
    client = FakeMaintenanceClient()
    maint = MemoryMaintenance(client=client)
    demoted = await maint.demote_stale_semantic_memory()
    assert demoted == 7
    query = client.queries[0][0]
    assert "memory_type <> 'decision'" in query
    assert "status = 'active'" in query
