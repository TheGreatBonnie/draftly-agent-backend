from __future__ import annotations

import json
from typing import Any

from draftly.persistence.repositories.github import (
    save_github_workflow,
    get_github_workflow_by_issue,
    list_github_workflows_record,
)


class FakeClient:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.row: dict[str, Any] | None = None
        self.rows: list[dict[str, Any]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "OK"

    async def fetch_one(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.executed.append((query, args))
        return self.row

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.executed.append((query, args))
        return self.rows


async def test_save_github_workflow_writes_meta_columns() -> None:
    client = FakeClient()
    client.row = {"id": "wf-1"}

    await save_github_workflow(
        org_id="o-1",
        workflow_id="ev-1",          # = run_id
        run_id="ev-1",               # NEW
        installation_id=123,
        owner="acme",
        repo="api",
        issue_number=482,
        title="OAuth Documentation Update",  # NEW
        actor="sarah.chen",                  # NEW
        db=client,
    )

    sql, params = client.executed[0]
    assert "INSERT INTO github_workflows" in sql
    assert "run_id" in sql
    assert "title" in sql
    assert "actor" in sql
    # Check that the title and actor are in the params
    assert "OAuth Documentation Update" in params
    assert "sarah.chen" in params


async def test_get_github_workflow_by_issue_returns_meta() -> None:
    client = FakeClient()
    client.row = {
        "id": "wf-1", "workflow_id": "ev-1", "run_id": "ev-1",
        "installation_id": 123, "owner": "acme", "repo": "api",
        "issue_number": 482, "status": "pending",
        "title": "OAuth Documentation Update", "actor": "sarah.chen",
    }
    row = await get_github_workflow_by_issue(owner="acme", repo="api", issue_number=482, db=client)
    assert row is not None
    assert row["title"] == "OAuth Documentation Update"
    assert row["actor"] == "sarah.chen"
    assert row["run_id"] == "ev-1"


async def test_list_github_workflows_record_shape() -> None:
    client = FakeClient()
    client.rows = [
        {
            "workflow_id": "ev-1", "run_id": "ev-1", "title": "Test PR",
            "owner": "acme", "repo": "api", "issue_number": 482,
            "actor": "sarah.chen", "status": "pending", "created_at": "2024-01-01T00:00:00Z"
        },
    ]
    # Need to also stub jobs and workflow_events fetches
    # Simulate the three-query pattern: first fetch_all for github_workflows
    # then fetch_all for jobs, then fetch_all for workflow_events
    call_count = 0
    async def fetch_all(query: str, *args: Any) -> list[dict[str, Any]]:
        nonlocal call_count
        call_count += 1
        client.executed.append((query, args))
        if call_count == 1:
            return client.rows
        elif call_count == 2:
            # jobs
            return [{"run_id": "ev-1", "status": "completed", "started_at": "2024-01-01T00:00:00Z", "completed_at": "2024-01-01T00:01:00Z"}]
        else:
            # workflow_events - terminal workflow_result + node_start for stages
            return [{"run_id": "ev-1", "type": "workflow_result", "payload": {"status": "COMPLETED"}, "ts": "2024-01-01T00:01:00Z"},
                    {"run_id": "ev-1", "type": "node_start", "node_id": "classify", "payload": {}, "ts": "2024-01-01T00:00:05Z"},
                    {"run_id": "ev-1", "type": "node_start", "node_id": "context", "payload": {}, "ts": "2024-01-01T00:00:10Z"},
                    {"run_id": "ev-1", "type": "node_start", "node_id": "research", "payload": {}, "ts": "2024-01-01T00:00:15Z"},
                    {"run_id": "ev-1", "type": "node_start", "node_id": "impact", "payload": {}, "ts": "2024-01-01T00:00:20Z"},
                    {"run_id": "ev-1", "type": "node_start", "node_id": "update", "payload": {}, "ts": "2024-01-01T00:00:25Z"},
                    {"run_id": "ev-1", "type": "node_start", "node_id": "evaluate", "payload": {}, "ts": "2024-01-01T00:00:30Z"},
                    {"run_id": "ev-1", "type": "node_start", "node_id": "deliver", "payload": {}, "ts": "2024-01-01T00:00:35Z"},
                    {"run_id": "ev-1", "type": "node_stop", "node_id": "classify", "payload": {"status": "COMPLETED"}, "ts": "2024-01-01T00:00:10Z"},
                    {"run_id": "ev-1", "type": "node_stop", "node_id": "context", "payload": {"status": "COMPLETED"}, "ts": "2024-01-01T00:00:15Z"},
                    {"run_id": "ev-1", "type": "node_stop", "node_id": "research", "payload": {"status": "COMPLETED"}, "ts": "2024-01-01T00:00:20Z"},
                    {"run_id": "ev-1", "type": "node_stop", "node_id": "impact", "payload": {"status": "COMPLETED"}, "ts": "2024-01-01T00:00:25Z"},
                    {"run_id": "ev-1", "type": "node_stop", "node_id": "update", "payload": {"status": "COMPLETED"}, "ts": "2024-01-01T00:00:30Z"},
                    {"run_id": "ev-1", "type": "node_stop", "node_id": "evaluate", "payload": {"status": "COMPLETED"}, "ts": "2024-01-01T00:00:35Z"},
                    {"run_id": "ev-1", "type": "node_stop", "node_id": "deliver", "payload": {"status": "COMPLETED"}, "ts": "2024-01-01T00:00:40Z"}]

    client.fetch_all = fetch_all  # type: ignore

    # We need to mock list_github_workflows_record to use our fetch_all
    # Since list_github_workflows_record creates its own db calls, let's just test it directly
    # by patching the db instance
    client.fetch = fetch_all
    rows = await list_github_workflows_record(org_id="o-1", db=client)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "ev-1"
    assert rows[0]["title"] == "Test PR"
    assert rows[0]["trigger_label"] == "PR #482"
    assert rows[0]["status"] == "completed"
    assert rows[0]["current_stage"] == "deliver"
    assert "stages" in rows[0]
    assert isinstance(rows[0]["stages"], list)
    assert rows[0]["time"] != "—"
