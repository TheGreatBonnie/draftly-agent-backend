"""Cutover-check script tests (offline, fake DatabaseClient — plan Task 10).

The cutover gate must be read-only: it may only SELECT rows, may never update
or cancel runs, prints a stable operator table, and exits nonzero while any
nonterminal documentation run remains.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest


@dataclass
class FakeDatabaseClientForCutover:
    """Mirror of DatabaseClient, scripted with joined workflow_runs rows."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    started: bool = False
    closed: bool = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def fetch_all(self, query: str, *args: Any) -> list[dict[str, Any]]:
        del args
        self.queries.append(query)
        return list(self.rows)

    async def fetch_one(self, query: str, *args: Any) -> Any:
        raise AssertionError("cutover check fetches many rows, not one")

    async def execute(self, query: str, *args: Any) -> str:
        raise AssertionError("cutover check must never write")


def _load_cutover_module() -> Any:
    script = Path(__file__).resolve().parents[2] / "scripts" / "check_documentation_cutover.py"
    spec = importlib.util.spec_from_file_location("cutover_under_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cutover_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    module = _load_cutover_module()
    fake = FakeDatabaseClientForCutover()
    monkeypatch.setattr(module, "DatabaseClient", lambda: fake)
    module._fake = fake
    return module


def _doc_run(run_id: str, status: str) -> dict[str, Any]:
    return {
        "id": run_id,
        "status": status,
        "workflow_key": "github_pr",
        "event_type": "pull_request.opened",
        "title": "Update docs",
        "repository": "acme/docs",
        "created_at": None,
    }


def _non_doc_run(run_id: str, status: str) -> dict[str, Any]:
    return {
        "id": run_id,
        "status": status,
        "workflow_key": "slack_support",
        "event_type": "slack.message",
        "title": "Support reply",
        "repository": None,
        "created_at": None,
    }


async def test_reports_run_ids_and_statuses_for_nonterminal_documentation_runs(
    cutover_module: Any,
) -> None:
    cutover_module._fake.rows = [
        _doc_run("run_queued", "queued"),
        _doc_run("run_running", "running"),
        _doc_run("run_review", "pending_review"),
        _doc_run("run_intervention", "pending_intervention"),
    ]
    runs = await cutover_module.find_pending_documentation_runs(cutover_module._fake)
    assert {r["id"] for r in runs} == {
        "run_queued",
        "run_running",
        "run_review",
        "run_intervention",
    }
    table = cutover_module.render_table(runs)
    assert "run_running" in table and "running" in table
    assert "run_intervention" in table and "pending_intervention" in table


async def test_exit_code_one_while_any_nonterminal_documentation_run_exists(
    cutover_module: Any,
) -> None:
    cutover_module._fake.rows = [_doc_run("run_running", "running")]
    runs = await cutover_module.find_pending_documentation_runs(cutover_module._fake)
    assert cutover_module.exit_code(runs) == 1


async def test_exit_code_zero_when_no_pending_documentation_runs(
    cutover_module: Any,
) -> None:
    cutover_module._fake.rows = []
    runs = await cutover_module.find_pending_documentation_runs(cutover_module._fake)
    assert cutover_module.exit_code(runs) == 0


async def test_ignores_non_documentation_runs(cutover_module: Any) -> None:
    cutover_module._fake.rows = [
        _doc_run("doc_running", "running"),
        _non_doc_run("support_running", "running"),
        _doc_run("doc_completed", "completed"),
    ]
    runs = await cutover_module.find_pending_documentation_runs(cutover_module._fake)
    assert [r["id"] for r in runs] == ["doc_running"]


async def test_queries_only_nonterminal_statuses(cutover_module: Any) -> None:
    cutover_module._fake.rows = [
        _doc_run("early_done", "completed"),
        _doc_run("early_failed", "failed"),
    ]
    runs = await cutover_module.find_pending_documentation_runs(cutover_module._fake)
    assert runs == []
    (query,) = cutover_module._fake.queries
    lowered = query.lower()
    for status in ("queued", "running", "pending_review", "pending_intervention"):
        assert status in lowered


async def test_cutover_check_is_read_only(cutover_module: Any) -> None:
    cutover_module._fake.rows = [_doc_run("run_running", "running")]
    await cutover_module.find_pending_documentation_runs(cutover_module._fake)
    (query,) = cutover_module._fake.queries
    lowered = query.lower()
    assert "select" in lowered
    for forbidden in ("insert", "update", "delete", "alter", "pause", "cancel"):
        assert forbidden not in lowered
