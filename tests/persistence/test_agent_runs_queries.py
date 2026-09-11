"""Read-side agent telemetry queries stay scoped and retain empty catalog entries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from draftly.persistence.repositories.agent_runs import AgentRunsRepository


class QueryDatabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch_all(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self.queries.append((sql, params))
        if "agent_steps s" in sql:
            return self.rows
        return self.rows


@pytest.mark.asyncio
async def test_summaries_include_zero_run_agents_and_scope_org() -> None:
    db = QueryDatabase([
        {
            "agent_id": "writer_agent",
            "step_surface": "documentation",
            "step_status": "completed",
            "run_id": "run-1",
            "run_status": "completed",
            "started_at": datetime.now(UTC),
            "event_type": "pull_request.opened",
        },
        {
            "agent_id": None,
            "step_surface": "",
            "step_status": "completed",
            "run_id": "legacy-1",
            "run_status": "completed",
            "started_at": datetime.now(UTC) - timedelta(days=2),
            "event_type": "legacy",
        },
    ])

    summaries = await AgentRunsRepository(db).list_agent_summaries(org_id="org-1")

    writer = next(item for item in summaries if item["id"] == "writer_agent")
    assert writer["runs_7d"] == 1
    assert writer["success_rate_7d"] == 1
    assert next(item for item in summaries if item["id"] == "classifier")["runs_7d"] == 0
    assert next(item for item in summaries if item["id"] == "legacy")["legacy_steps"] == 1
    assert all("org-1" in params for _, params in db.queries)


@pytest.mark.asyncio
async def test_agent_runs_use_opaque_cursor_and_org_filter() -> None:
    db = QueryDatabase([
        {"run_id": "run-2", "started_at": datetime.now(UTC)},
        {"run_id": "run-1", "started_at": datetime.now(UTC) - timedelta(minutes=1)},
    ])

    runs, cursor = await AgentRunsRepository(db).list_agent_runs(
        org_id="org-1", agent_id="writer_agent", limit=1
    )

    assert [item["run_id"] for item in runs] == ["run-2"]
    assert cursor
    assert "org-1" in db.queries[-1][1]
    assert "writer_agent" in db.queries[-1][1]


@pytest.mark.asyncio
async def test_unknown_agent_detail_returns_none() -> None:
    assert await AgentRunsRepository(QueryDatabase([])).get_agent_detail(
        org_id="org-1", agent_id="not-real"
    ) is None


@pytest.mark.asyncio
async def test_catalog_surface_filter_does_not_confuse_run_surface() -> None:
    db = QueryDatabase([{
        "agent_id": "writer_agent", "step_surface": "pull_request",
        "step_status": "completed", "run_id": "run-1", "run_status": "completed",
        "started_at": datetime.now(UTC), "event_type": "pull_request.opened",
    }])
    summaries = await AgentRunsRepository(db).list_agent_summaries(
        org_id="org-1", surface="documentation"
    )
    assert next(item for item in summaries if item["id"] == "writer_agent")["runs_7d"] == 1
